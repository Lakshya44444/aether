"use client";

import { motion } from "motion/react";
import { useEffect, useState } from "react";
import { API_BASE, cn } from "@/lib/utils";
import measured from "@/lib/measured.json";

const LADDER = ["ALLOW", "WARN", "REDACT", "ESCALATE", "BLOCK"] as const;
type Decision = (typeof LADDER)[number];

const ON_BG: Record<Decision, string> = {
  ALLOW: "bg-allow",
  WARN: "bg-warn",
  REDACT: "bg-redact",
  ESCALATE: "bg-escalate",
  BLOCK: "bg-block",
};

/* One sentence, three consequences. The output text never changes.

   Sample, contexts and the offline fallback all come from measured.json, which
   scripts/export_metrics.py produces by driving the real pipeline. Hand-copying a
   "recorded run" into this file is how the fallback ends up describing behaviour the
   system no longer has. */
const { input: INPUT, output: OUTPUT, highlight: HIGHLIGHT, contexts: CONTEXTS } =
  measured.sample;

/* The sentence was literal JSX here while OUTPUT went to the API, so changing the
   sample made the page show one thing and evaluate another. Split around the exported
   fragment instead. */
const HL_AT = OUTPUT.indexOf(HIGHLIGHT);
const [BEFORE, AFTER] =
  HL_AT === -1 ? [OUTPUT, ""] : [OUTPUT.slice(0, HL_AT), OUTPUT.slice(HL_AT + HIGHLIGHT.length)];

/* Used only when the API is unreachable — and the UI says so rather than passing
   these off as live. */
const RECORDED = measured.sample.recorded as Record<
  string,
  { decision: Decision; reason: string; scores: Record<string, number> }
>;

type Result = { decision: Decision; reason: string; scores: Record<string, number>; live: boolean };
type Loaded = Result & { ctx: string };

export function Decider() {
  const [ctx, setCtx] = useState<(typeof CONTEXTS)[number]["key"]>("customer_support");
  const [res, setRes] = useState<Loaded | null>(null);

  // The request lives in the effect rather than in a callback the effect calls, so
  // nothing sets state on the synchronous path, and a context switched mid-flight
  // discards the reply that is arriving for the previous one.
  useEffect(() => {
    let cancelled = false;
    const c = CONTEXTS.find((x) => x.key === ctx)!;

    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/evaluate`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            input_text: INPUT,
            output_text: OUTPUT,
            use_case: c.key,
            action: c.action,
            session_id: `landing-${c.key}-${Date.now()}`,
          }),
        });
        if (!r.ok) throw new Error(String(r.status));
        const j = await r.json();
        const scores: Record<string, number> = {};
        for (const d of j.trace.detection_results ?? []) scores[d.category] = d.score;
        if (cancelled) return;
        setRes({ decision: j.decision, reason: j.reason, scores, live: true, ctx });
      } catch {
        if (cancelled) return;
        setRes({ ...RECORDED[ctx], live: false, ctx });
      }
    })();

    return () => { cancelled = true; };
  }, [ctx]);

  // The result is stale until it carries the context currently selected.
  const settled = res?.ctx === ctx ? res : null;
  const active = settled?.decision;

  return (
    <section
      aria-label="Live decision demo"
      className="relative z-10 mt-10 overflow-hidden border border-ink bg-card sm:mt-14"
    >
      <div className="flex items-center gap-2.5 border-b border-ink bg-sunk px-4 py-2.5">
        <span className="flex gap-1.5">
          {[0, 1, 2].map((i) => <i key={i} className="size-2.5 dot bg-rule" />)}
        </span>
        <span className="font-mono text-xs text-mute">POST /api/evaluate</span>
        <span className="ml-auto label text-mute">
          {!settled ? "evaluating…" : settled.live ? "live" : "API offline — recorded run"}
        </span>
      </div>

      <div className="grid lg:grid-cols-[1.05fr_0.95fr]">
        {/* Input side */}
        <div className="border-b border-rule p-6 lg:border-b-0 lg:border-r">
          <Label>The model said</Label>
          <blockquote className="m-0 border border-rule bg-paper px-4 py-3.5 font-mono text-[14px] leading-relaxed">
            {BEFORE}
            {HL_AT !== -1 && (
              <b className="border-b-2 border-warn font-semibold">{HIGHLIGHT}</b>
            )}
            {AFTER}
          </blockquote>
          <p className="mt-2 text-xs text-mute">This sentence never changes. Only the context below changes.</p>

          <div className="mt-5">
            <Label>Who is asking</Label>
            <div role="group" aria-label="Use case" className="grid grid-cols-3 gap-1 border border-rule bg-paper p-1">
              {CONTEXTS.map((c) => (
                <button
                  key={c.key}
                  type="button"
                  aria-pressed={ctx === c.key}
                  onClick={() => setCtx(c.key)}
                  className={cn(
 "cursor-pointer px-1.5 py-2.5 text-[12.5px] leading-tight transition",
                    ctx === c.key
                      ? "bg-ink text-paper"
                      : "text-mute hover:text-ink-2",
                  )}
                >
                  {c.label}
                  <small className="mt-0.5 block font-mono text-[10.5px] opacity-70">{c.sub}</small>
                </button>
              ))}
            </div>
            <p className="mt-2 text-xs text-mute">Same words, escalating consequence — a reply, a record, a payment.</p>
          </div>
        </div>

        {/* Verdict side */}
        <div className="flex flex-col p-6">
          <Label>Decision</Label>
          <div className="grid grid-cols-5 gap-0.5">
            {LADDER.map((k) => {
              const on = active === k;
              return (
                <motion.div
                  key={k}
                  animate={{ y: on ? -3 : 0 }}
                  transition={{ type: "spring", stiffness: 420, damping: 26 }}
                  className={cn(
 "grid h-11 place-items-center border label transition-colors duration-300",
                    on
                      ? `${ON_BG[k]} border-ink text-paper`
                      : "border-rule bg-paper text-mute",
                  )}
                >
                  {k}
                </motion.div>
              );
            })}
          </div>

          <div className="mt-5">
            <Label>Why</Label>
            <p className="min-h-[76px] border border-rule bg-paper px-4 py-3 text-[13.5px] leading-relaxed text-ink-2">
              {res?.reason ?? "Running the first evaluation…"}
            </p>
          </div>

          <div className="mt-5 grid gap-2.5">
            {["factuality", "privacy", "bias", "cost"].map((c) => {
              const v = res?.scores?.[c] ?? 0;
              return (
                <div key={c} className="grid grid-cols-[74px_1fr_40px] items-center gap-3">
                  <span className="label text-mute">{c}</span>
                  <span className="h-1.5 overflow-hidden dot bg-rule-2">
                    <motion.span
                      className="block h-full dot bg-ink-2"
                      initial={{ width: 0 }}
                      animate={{ width: `${Math.round(v * 100)}%` }}
                      transition={{ duration: 0.6, ease: [0.22, 0.68, 0.28, 1] }}
                    />
                  </span>
                  <span className="text-right font-mono text-xs text-ink-2">{res ? v.toFixed(2) : "—"}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <span className="mb-2 block label text-mute">
      {children}
    </span>
  );
}
