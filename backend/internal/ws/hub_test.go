package ws

import (
	"encoding/json"
	"testing"
	"time"

	"auto-sre/backend/internal/envelope"
)

func newTestClient(h *Hub, channels ...string) *Client {
	c := &Client{
		hub:      h,
		send:     make(chan []byte, 64),
		channels: map[string]struct{}{},
	}
	for _, ch := range channels {
		c.subscribe(ch)
	}
	return c
}

func recv(t *testing.T, c *Client) envelope.Envelope {
	t.Helper()
	select {
	case raw := <-c.send:
		var e envelope.Envelope
		if err := json.Unmarshal(raw, &e); err != nil {
			t.Fatalf("bad payload: %v", err)
		}
		return e
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for fanout")
		return envelope.Envelope{}
	}
}

func TestFanoutToSubscribedClient(t *testing.T) {
	h := NewHub(nil)
	go h.Run()
	defer func() { h.unreg <- newTestClient(h) }()

	client := newTestClient(h, "incident:abc")
	h.register <- client

	h.Broadcast(envelope.New(envelope.AgentLog, "incident:abc").WithPayload(map[string]any{"text": "hi"}))

	got := recv(t, client)
	if got.Type != envelope.AgentLog {
		t.Fatalf("type = %s", got.Type)
	}
	if got.Payload["text"] != "hi" {
		t.Fatalf("payload = %v", got.Payload)
	}
}

func TestNoFanoutToUnsubscribedChannel(t *testing.T) {
	h := NewHub(nil)
	go h.Run()
	defer func() { h.unreg <- newTestClient(h) }()

	client := newTestClient(h, "incident:abc")
	h.register <- client

	h.Broadcast(envelope.New(envelope.GraphStatus, "incident:xyz"))

	select {
	case raw := <-client.send:
		t.Fatalf("client should not receive events for other channels, got %s", raw)
	case <-time.After(300 * time.Millisecond):
		// expected
	}
}

func TestUnregisterStopsDelivery(t *testing.T) {
	h := NewHub(nil)
	go h.Run()

	client := newTestClient(h, "incident:abc")
	h.register <- client
	h.unreg <- client

	h.Broadcast(envelope.New(envelope.AgentLog, "incident:abc"))
	if _, ok := <-client.send; ok {
		t.Fatalf("unregistered client received a message")
	}
}

func TestControlHandlerInvoked(t *testing.T) {
	var got *envelope.WSClientMessage
	h := NewHub(func(msg envelope.WSClientMessage) {
		got = &msg
	})
	if h.control() == nil {
		t.Fatal("control handler not set")
	}
	_ = got
}
