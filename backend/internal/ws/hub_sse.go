package ws

import (
	"sync"
)

// sseSubs holds per-channel SSE fan-out sinks. The same envelope that is
// pushed to WebSocket clients is mirrored to SSE subscribers so the HTMX
// dashboard and the React command center observe the identical event stream.
type sseSink struct {
	mu  sync.Mutex
	sub map[string]map[chan []byte]struct{}
}

func newSSESink() *sseSink {
	return &sseSink{sub: map[string]map[chan []byte]struct{}{}}
}

func (s *sseSink) add(channel string, ch chan []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.sub[channel] == nil {
		s.sub[channel] = map[chan []byte]struct{}{}
	}
	s.sub[channel][ch] = struct{}{}
}

func (s *sseSink) remove(channel string, ch chan []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if set := s.sub[channel]; set != nil {
		delete(set, ch)
		if len(set) == 0 {
			delete(s.sub, channel)
		}
	}
}

func (s *sseSink) fanout(channel string, payload []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for ch := range s.sub[channel] {
		select {
		case ch <- payload:
		default:
			// Slow SSE consumer: drop rather than stall the fabric.
		}
	}
	for ch := range s.sub["*"] {
		select {
		case ch <- payload:
		default:
		}
	}
}

// SubscribeSSE returns a channel receiving marshalled envelopes for the given
// channels plus a cleanup func. Callers must invoke cleanup on disconnect.
func (h *Hub) SubscribeSSE(channels []string) (<-chan []byte, func()) {
	h.mu.Lock()
	if h.sse == nil {
		h.sse = newSSESink()
	}
	h.mu.Unlock()

	ch := make(chan []byte, 128)
	for _, c := range channels {
		h.sse.add(c, ch)
	}
	done := make(chan struct{})
	var once sync.Once
	return ch, func() {
		once.Do(func() {
			for _, c := range channels {
				h.sse.remove(c, ch)
			}
			close(done)
		})
	}
}
