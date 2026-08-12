package api

import (
	"encoding/json"
	"time"
)

func marshalAny(v any) ([]byte, error) { return json.Marshal(v) }

func nowISO() string { return time.Now().UTC().Format(time.RFC3339Nano) }

func strField(m map[string]any, key string) string {
	s, _ := m[key].(string)
	return s
}
