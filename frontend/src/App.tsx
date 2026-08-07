export default function App() {
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-slate-800 px-6 py-3">
        <h1 className="text-lg font-semibold tracking-tight text-white">
          Auto-SRE <span className="text-accent">Command Center</span>
        </h1>
        <span className="rounded-full bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-400">
          ● Online
        </span>
      </header>
      <main className="flex flex-1 items-center justify-center text-slate-400">
        <p>Bootstrapping command center…</p>
      </main>
    </div>
  );
}
