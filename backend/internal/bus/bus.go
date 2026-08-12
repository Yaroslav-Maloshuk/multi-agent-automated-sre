// Package bus is the NATS fabric: the Go gateway publishes worker work-queue
// requests, HITL reviews and control signals, and fans worker events out to
// WebSocket subscribers. NATS JetStream (in the worker pool) replaces Celery +
// Redis Pub/Sub for ultra-low-latency agentic dispatch.
package bus

import (
	"encoding/json"
	"fmt"

	"github.com/nats-io/nats.go"

	"auto-sre/backend/internal/envelope"
)

type Bus struct {
	nc             *nats.Conn
	eventsPrefix   string
	runRequest     string
	hitlPrefix     string
	ctrlPrefix     string
	runSub         *nats.Subscription
	eventsHandlers []func(envelope.Envelope)
}

func Connect(url string) (*Bus, error) {
	nc, err := nats.Connect(url,
		nats.Name("auto-sre-gateway"),
		nats.MaxReconnects(-1),
	)
	if err != nil {
		return nil, err
	}
	return &Bus{
		nc:           nc,
		eventsPrefix: "sre.events",
		runRequest:   "sre.run.request",
		hitlPrefix:   "sre.hitl.review",
		ctrlPrefix:   "sre.graph.control",
	}, nil
}

func (b *Bus) Ping() error {
	return b.nc.FlushTimeout(2 * 1e9)
}

func (b *Bus) Close() {
	if b.runSub != nil {
		_ = b.runSub.Unsubscribe()
	}
	b.nc.Close()
}

func (b *Bus) PublishEnvelope(env envelope.Envelope) error {
	data, err := env.Marshal()
	if err != nil {
		return err
	}
	return b.nc.Publish(b.subject(env.Channel), data)
}

func (b *Bus) subject(channel string) string {
	return b.eventsPrefix + "." + channel
}

func (b *Bus) PublishRunRequest(run envelope.RunRequest) error {
	data, err := json.Marshal(run)
	if err != nil {
		return err
	}
	return b.nc.Publish(b.runRequest, data)
}

func (b *Bus) PublishReview(incidentID string, review envelope.HITLReview) error {
	data, err := json.Marshal(review)
	if err != nil {
		return err
	}
	return b.nc.Publish(fmt.Sprintf("%s.%s", b.hitlPrefix, incidentID), data)
}

func (b *Bus) PublishControl(incidentID string, ctrl envelope.ControlMessage) error {
	data, err := json.Marshal(ctrl)
	if err != nil {
		return err
	}
	return b.nc.Publish(fmt.Sprintf("%s.%s", b.ctrlPrefix, incidentID), data)
}

// SubscribeEvents drains every envelope published by the worker pool. The
// callback is invoked from the NATS goroutine; keep it cheap (fan-out).
func (b *Bus) SubscribeEvents(handler func(envelope.Envelope)) error {
	sub, err := b.nc.Subscribe(b.eventsPrefix+".>", func(msg *nats.Msg) {
		env, err := envelope.Parse(msg.Data)
		if err != nil {
			return
		}
		handler(env)
	})
	if err != nil {
		return err
	}
	_ = sub.SetPendingLimits(100_000, 256*1024*1024)
	return b.nc.Flush()
}

func (b *Bus) QueueSubscribeRunRequest(handler func(envelope.RunRequest)) error {
	sub, err := b.nc.QueueSubscribe(b.runRequest, "sre-workers", func(msg *nats.Msg) {
		var run envelope.RunRequest
		if err := json.Unmarshal(msg.Data, &run); err != nil {
			return
		}
		handler(run)
	})
	if err != nil {
		return err
	}
	_ = sub.SetPendingLimits(10_000, 64*1024*1024)
	return b.nc.Flush()
}

func (b *Bus) NATS() *nats.Conn { return b.nc }
