import { useEffect, useRef } from "react";
import type { ConsoleLine } from "../store";
import { fmtTime } from "../lib/format";
import { eventStyle } from "./Badge";

interface ConsoleProps {
  lines: ConsoleLine[];
  heightClass?: string;
  empty?: string;
}

export default function Console({ lines, heightClass = "h-64", empty = "No events yet — create and run an incident." }: ConsoleProps) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [lines]);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  return (
    <div
      ref={ref}
      onScroll={onScroll}
      className={`overflow-y-auto rounded-md border border-slate-800 bg-black/40 p-3 font-mono text-xs leading-relaxed ${heightClass}`}
    >
      {lines.length === 0 ? (
        <div className="text-slate-600">{empty}</div>
      ) : (
        <ul className="space-y-0.5">
          {lines.map((line) => (
            <li key={line.id} className="flex gap-2 whitespace-nowrap">
              <span className="shrink-0 text-slate-600">{fmtTime(line.ts)}</span>
              <span className={`shrink-0 font-semibold ${eventStyle(line.type)}`}>
                {line.type}
              </span>
              <span className="truncate text-slate-400">{line.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
