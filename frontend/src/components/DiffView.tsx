import { DiffEditor, loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";

loader.config({ monaco });

interface DiffViewProps {
  original: string;
  modified: string;
  language?: string;
  height?: number;
  originalLabel?: string;
  modifiedLabel?: string;
}

export default function DiffView({
  original,
  modified,
  language = "plaintext",
  height = 220,
  originalLabel = "before",
  modifiedLabel = "after",
}: DiffViewProps) {
  return (
    <div className="overflow-hidden rounded-md border border-slate-800">
      <DiffEditor
        original={original || " "}
        modified={modified || " "}
        language={language}
        theme="vs-dark"
        height={height}
        options={{
          readOnly: true,
          renderSideBySide: true,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          fontSize: 11,
          automaticLayout: true,
          originalEditable: false,
          renderOverviewRuler: false,
        }}
      />
      <div className="flex items-center justify-between border-t border-slate-800 bg-black/40 px-3 py-1 text-[10px] text-slate-500">
        <span>{originalLabel}</span>
        <span>{modifiedLabel}</span>
      </div>
    </div>
  );
}
