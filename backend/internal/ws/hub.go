// Package ws implements the bidirectional WebSocket command center. Every
// envelope published by the Rust workers (via NATS) is fanned out to clients
// subscribed to the matching logical channel — zero polling.
package ws

import (
	"encoding/json"
	"net/http"
	"sync"
	"time"

	"github.com/gorilla/websocket"

	"auto-sre/backend/internal/envelope"
)

type ControlHandler func(envelope.WSClientMessage)

type Hub struct {
	mu        sync.RWMutex
	channels  map[string]map[*Client]struct{}
	broadcast chan envelope.Envelope
	register  chan *Client
	unreg     chan *Client
	onControl ControlHandler
}

func (h *Hub) SetControlHandler(handler ControlHandler) {
	h.mu.Lock()
	h.onControl = handler
	h.mu.Unlock()
}

func (h *Hub) control() ControlHandler {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return h.onControl
}

type Client struct {
	hub      *Hub
	conn     *websocket.Conn
	send     chan []byte
	channels map[string]struct{}
	mu       sync.Mutex
}

var upgrader = websocket.Upgrader{
	ReadBufferSize:  4096,
	WriteBufferSize: 4096,
	CheckOrigin: func(r *http.Request) bool {
		return true // CORS is enforced by the gateway middleware.
	},
}

func NewHub(onControl ControlHandler) *Hub {
	return &Hub{
		channels:  map[string]map[*Client]struct{}{},
		broadcast: make(chan envelope.Envelope, 4096),
		register:  make(chan *Client),
		unreg:     make(chan *Client),
		onControl: onControl,
	}
}

func (h *Hub) Run() {
	for {
		select {
		case c := <-h.register:
			h.mu.Lock()
			for ch := range c.channels {
				h.addLocked(ch, c)
			}
			h.mu.Unlock()
		case c := <-h.unreg:
			h.mu.Lock()
			for ch := range c.channels {
				h.removeLocked(ch, c)
			}
			h.mu.Unlock()
			close(c.send)
		case env := <-h.broadcast:
			h.mu.RLock()
			clients := h.channels[env.Channel]
			payload, err := env.Marshal()
			h.mu.RUnlock()
			if err != nil {
				continue
			}
			for c := range clients {
				select {
				case c.send <- payload:
				default:
					// Slow consumer: drop the client rather than stall the fabric.
					go h.unregister(c)
				}
			}
		}
	}
}

func (h *Hub) addLocked(channel string, c *Client) {
	if h.channels[channel] == nil {
		h.channels[channel] = map[*Client]struct{}{}
	}
	h.channels[channel][c] = struct{}{}
}

func (h *Hub) unregister(c *Client) {
	h.unreg <- c
}

func (h *Hub) removeLocked(channel string, c *Client) {
	if set := h.channels[channel]; set != nil {
		delete(set, c)
		if len(set) == 0 {
			delete(h.channels, channel)
		}
	}
}

func (h *Hub) Broadcast(env envelope.Envelope) {
	h.broadcast <- env
}

func (h *Hub) ServeWS(w http.ResponseWriter, r *http.Request, initialChannels []string) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	c := &Client{
		hub:      h,
		conn:     conn,
		send:     make(chan []byte, 256),
		channels: map[string]struct{}{},
	}
	for _, ch := range initialChannels {
		c.channels[ch] = struct{}{}
	}
	h.register <- c
	go c.writePump()
	go c.readPump()
}

func (c *Client) subscribe(channel string) {
	c.hub.mu.Lock()
	if _, ok := c.channels[channel]; ok {
		c.hub.mu.Unlock()
		return
	}
	c.channels[channel] = struct{}{}
	c.hub.addLocked(channel, c)
	c.hub.mu.Unlock()
}

func (c *Client) unsubscribe(channel string) {
	c.hub.mu.Lock()
	delete(c.channels, channel)
	c.hub.removeLocked(channel, c)
	c.hub.mu.Unlock()
}

func (c *Client) readPump() {
	defer func() {
		c.hub.unreg <- c
		_ = c.conn.Close()
	}()
	c.conn.SetReadLimit(1 << 20)
	_ = c.conn.SetReadDeadline(time.Now().Add(90 * time.Second))
	c.conn.SetPongHandler(func(string) error {
		return c.conn.SetReadDeadline(time.Now().Add(90 * time.Second))
	})
	for {
		_, raw, err := c.conn.ReadMessage()
		if err != nil {
			return
		}
		var msg envelope.WSClientMessage
		if err := jsonUnmarshal(raw, &msg); err != nil {
			continue
		}
		switch msg.Action {
		case "subscribe":
			if msg.Channel != nil {
				c.subscribe(*msg.Channel)
			}
		case "unsubscribe":
			if msg.Channel != nil {
				c.unsubscribe(*msg.Channel)
			}
		case "ping":
			pong := envelope.New(envelope.EventType("system.pong"), envelope.ChannelSystem)
			raw, _ := pong.Marshal()
			select {
			case c.send <- raw:
			default:
			}
		default:
			if h := c.hub.control(); h != nil {
				h(msg)
			}
		}
	}
}

func (c *Client) writePump() {
	ticker := time.NewTicker(30 * time.Second)
	defer func() {
		ticker.Stop()
		_ = c.conn.Close()
	}()
	for {
		select {
		case msg, ok := <-c.send:
			_ = c.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if !ok {
				_ = c.conn.WriteMessage(websocket.CloseMessage, []byte{})
				return
			}
			if err := c.conn.WriteMessage(websocket.TextMessage, msg); err != nil {
				return
			}
		case <-ticker.C:
			_ = c.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if err := c.conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
		}
	}
}

func jsonUnmarshal(raw []byte, v any) error {
	return json.Unmarshal(raw, v)
}
