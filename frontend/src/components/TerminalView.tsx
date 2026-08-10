import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

interface TerminalViewProps {
  lines: string[];
  height?: string;
  empty?: string;
}

export default function TerminalView({ lines, height = "h-64", empty = "No output yet." }: TerminalViewProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const writtenRef = useRef(0);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const term = new Terminal({
      convertEol: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      fontSize: 12,
      theme: {
        background: "#060a14",
        foreground: "#cbd5e1",
      },
      scrollback: 2000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    try {
      fit.fit();
    } catch {
      // host may be hidden at mount; the resize listener will re-fit
    }
    termRef.current = term;
    fitRef.current = fit;
    writtenRef.current = 0;
    return () => {
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    const pending = lines.slice(writtenRef.current);
    for (const line of pending) {
      term.writeln(line || " ");
    }
    writtenRef.current = lines.length;
  }, [lines]);

  useEffect(() => {
    const onResize = () => {
      const fit = fitRef.current;
      if (fit) {
        try {
          fit.fit();
        } catch {
          // ignore
        }
      }
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return (
    <div
      ref={hostRef}
      className={`overflow-hidden rounded-md border border-slate-800 bg-[#060a14] p-1 ${height}`}
    >
      {lines.length === 0 ? <div className="p-2 font-mono text-xs text-slate-600">{empty}</div> : null}
    </div>
  );
}
