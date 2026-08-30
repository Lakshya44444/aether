import Link from "next/link";
import { Decider } from "@/components/ui/decider";
import { Counter, Panel, Reveal, TracingBeam } from "@/components/ui/motion-primitives";
import measured from "@/lib/measured.json";
import { API_BASE, cn } from "@/lib/utils";

const NAV = [
  ["Decisions", "#states"],
  ["Checks", "#checks"],
  ["Pipeline", "#pipeline"],
  ["Audit", "#audit"],
  ["Measured", "#measured"],
] as const;

const STATES = [
  { n: "01", k: "Allow", c: "text-allow", h: "Ship it", p: "Nothing crossed a threshold. The row is still logged." },
  { n: "02", k: "Warn", c: "text-warn", h: "Ship with a flag", p: "Something scored, but not enough to stop a reversible action." },
  { n: "03", k: "Redact", c: "text-redact", h: "Mask and ship", p: "PII is masked at the exact offsets the detector reported." },
  { n: "04", k: "Escalate", c: "text-escalate", h: "Ask a human", p: "Held for review, with the signals that raised it attached." },
  { n: "05", k: "Block", c: "text-block", h: "Stop", p: "The action does not happen. Correction may be attempted first." },
];

/* Ceilings come from measured.json, which reads them out of src/config.py. Typed as
   literals here they became a claim about a cap the code might no longer apply. */
const FACT_CEILING = measured.ceilings.factualityHeuristic.toFixed(2);
const INJ_CEILING = measured.ceilings.injection.toFixed(2);

/* Held-out recall beside recall on phrasings nothing was tuned against. Aggregated in
   scripts/export_metrics.py, where the underlying counts still exist -- averaging two
   rounded recalls is not the same number. */
const RECALL_ROWS = measured.recallComparison;
const WORST = RECALL_ROWS.reduce((a, b) => (b.unseen < a.unseen ? b : a), RECALL_ROWS[0]);
const worstUnseenRecall = WORST.unseen;
const worstUnseenName = WORST.name;

const CHECKS = [
  { k: "Factuality", h: "Is the claim supported?", p: `Checks claims against supplied context documents, or samples an independent judge model and compares the answers. With no judge configured it falls back to a surface heuristic, capped at ${FACT_CEILING} so a guess can warn but never block.`, wide: true },
  { k: "Privacy", h: "What leaked?", p: "Scored by the worst thing found, not by how many matches landed. Private and loopback ranges are deliberately not treated as personal data." },
  { k: "Bias", h: "Aimed at a person?", p: "Patterns require a human target and skip negated mentions, so writing a policy against bias is not itself scored as bias." },
  { k: "Cost", h: "Is this session burning money?", p: "Tracks spend and repeated prompts per session. A repeated identical prompt means the caller is retrying a failing interaction — the signal that drives multi-turn escalation." },
  { k: "Injection", h: "Is the prompt trying to take over?", p: `Five published families — instruction override, role reassignment, system-prompt exfiltration, guardrail evasion, delimiter smuggling. Capped at ${INJ_CEILING} so a regex escalates to a human rather than refusing traffic on its own.` },
];

const STEPS = [
  { h: "Resolve context", p: <>Load the policy for this use case and read its risk tier. Context comes first — without it there is no tier to route on.</> },
  { h: "Route verification depth", p: <>Tier and action impact pick <Code>shallow</Code>, <Code>medium</Code> or <Code>deep</Code>. An irreversible action always routes deep, whatever the use case&rsquo;s own tier says.</> },
  { h: "Run the five checks", p: <>Four read the completion and one reads the prompt. Each is bounded by the policy&rsquo;s latency budget. A detector that fails produced no signal, so the declared <Code>fail_mode</Code> decides — a crash never becomes a silent approval.</> },
  { h: "Assemble the risk picture", p: <>This turn&rsquo;s scores, accumulated session exposure, and the action&rsquo;s impact and reversibility stay separate fields rather than collapsing into one number.</> },
  { h: "Apply policy", p: <>Thresholds are indexed by category and impact class, every one on the detector&rsquo;s own [0,&nbsp;1] scale. An irreversible action carrying a live signal is never released below BLOCK.</> },
  { h: "Correct, then re-verify", p: <>If correction is attempted, the revised text goes back through the same detectors and the same policy. A corrector&rsquo;s own claim of success is not accepted as evidence.</> },
  { h: "Write the audit row", p: <>Every decision is logged — allowed ones included — hash-chained to the row before it.</> },
];

export default function Page() {
  return (
    <>
      {/* ── Nav ── */}
      <nav className="sticky top-0 z-50 border-b border-ink bg-paper">
        <div className="mx-auto flex h-[62px] w-full max-w-[1120px] items-center gap-6 px-5 sm:px-12">
          <Brand />
          <div className="flex-1" />
          <div className="hidden gap-6 md:flex">
            {NAV.map(([label, href]) => (
              <a key={href} href={href} className="label text-mute transition-colors hover:text-block">{label}</a>
            ))}
          </div>
          <a href="/console.html" className="inline-flex h-9 items-center border border-block bg-block px-4 label text-paper transition hover:bg-ink hover:border-ink">
            Open console
          </a>
        </div>
      </nav>

      {/* ── Hero ── */}
      <header className="relative pb-14 pt-14 sm:pb-20 sm:pt-24">
        <div className="relative z-10 mx-auto w-full max-w-[1120px] px-5 sm:px-12">
          <span className="inline-flex h-7 items-center gap-2.5 border border-ink/25 rule-dashed bg-card px-3 label text-ink-2">
            <i className="animate-blink size-1.5 dot bg-block" />
            Runtime control plane
          </span>

          <h1 className="mt-6 max-w-[15ch] font-display text-[clamp(40px,7.4vw,78px)] leading-[1.02] tracking-[-0.02em]">
            The same answer.
            <span className="block text-mute">Three different verdicts.</span>
          </h1>

          <p className="mt-5 max-w-[56ch] text-lg text-ink-2">
            Aether reads what your AI produced, weighs it against the action it is about to take,
            and returns one of five decisions — with a reason a human can read and an audit row that
            cannot be quietly edited.
          </p>

          <div className="mt-7 flex flex-wrap gap-3">
            <a href="#states" className="inline-flex h-11 items-center border border-block bg-block px-6 label text-paper transition hover:bg-ink hover:border-ink">
              See how it decides
            </a>
            <a href="/console.html" className="inline-flex h-11 items-center border border-ink px-6 label transition hover:bg-ink hover:text-paper">
              Open the console
            </a>
          </div>

          <Decider />
        </div>
      </header>

      {/* ── Decision ladder ── */}
      <Section id="states" tint>
        <Eyebrow>Decision ladder</Eyebrow>
        <SectionTitle>Five states, ordered by how much they cost a human.</SectionTitle>
        <Lede>
          A binary allow/block forces a choice between shipping noise and shipping risk. Aether resolves
          to the least disruptive state that still contains the problem, and every state above ALLOW
          carries the reason that produced it.
        </Lede>
        <div className="mt-10 grid gap-3.5 sm:grid-cols-2 lg:grid-cols-5">
          {STATES.map((s, i) => (
            <Reveal key={s.k} delay={i * 0.05}>
              <Panel className="h-full">
                <p className={cn("label", s.c)}>{s.n} — {s.k}</p>
                <h3 className="mt-3 font-display text-lg">{s.h}</h3>
                <p className="mt-2 text-sm text-ink-2">{s.p}</p>
              </Panel>
            </Reveal>
          ))}
        </div>
      </Section>

      {/* ── Checks ── */}
      <Section id="checks">
        <Eyebrow>Detection</Eyebrow>
        <SectionTitle>Five checks, run against every request.</SectionTitle>
        <Lede>
          Each returns a score in [0,&nbsp;1] and the exact spans that caused it. Scores are never summed
          into one number — the policy engine reads them separately, because a privacy leak and a cost
          overrun are not the same event and do not deserve the same response.
        </Lede>
        <div className="mt-10 grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
          {CHECKS.map((c, i) => (
            <Reveal key={c.k} delay={i * 0.05} className={c.wide ? "lg:col-span-2" : ""}>
              <Panel className="h-full">
                <p className="label text-mute">{c.k}</p>
                <h3 className="mt-3 font-display text-xl">{c.h}</h3>
                <p className="mt-2 text-[14.5px] text-ink-2">{c.p}</p>
              </Panel>
            </Reveal>
          ))}
        </div>
      </Section>

      {/* ── Pipeline ── */}
      <Section id="pipeline" tint>
        <Eyebrow>Request path</Eyebrow>
        <SectionTitle>How one decision is made.</SectionTitle>
        <Lede>These run in order — each step needs what the one before it produced.</Lede>
        <div className="mt-11">
          <TracingBeam>
            {STEPS.map((s, i) => (
              <Reveal key={s.h} className="relative pb-8 last:pb-0">
                <div className="absolute -left-12 top-0 grid size-8 place-items-center border border-rule bg-card font-mono text-xs font-semibold text-mute">
                  {i + 1}
                </div>
                <h3 className="font-display text-[18px]">{s.h}</h3>
                <p className="mt-1.5 max-w-[64ch] text-[14.5px] text-ink-2">{s.p}</p>
              </Reveal>
            ))}
          </TracingBeam>
        </div>
      </Section>

      {/* ── Policy ── */}
      <Section id="policy">
        <Eyebrow>Governance</Eyebrow>
        <SectionTitle>Policy is a file, not a rewrite.</SectionTitle>
        <Lede>
          Each use case owns a JSON policy. Thresholds nest by impact class, so the number governing a
          draft email is visibly not the number governing a payment — and you can read the file without
          recomputing anything in your head.
        </Lede>
        <Reveal className="mt-9">
          <div className="overflow-hidden border border-ink bg-ink">
            <div className="border-b border-paper/20 px-4 py-2.5 label text-paper/55">
              {measured.policyExcerpt.path}
            </div>
            <pre className="overflow-x-auto p-5 font-mono text-[12.5px] leading-[1.75] text-[#E8DFCD]">
<code>{measured.policyExcerpt.text}</code>
            </pre>
          </div>
        </Reveal>
      </Section>

      {/* ── Audit ── */}
      <Section id="audit" tint>
        <Eyebrow>Audit</Eyebrow>
        <SectionTitle>Tamper-evident, and honest about what that means.</SectionTitle>
        <Lede>
          Every row stores the hash of the row before it, so editing or removing a decision after the
          fact breaks the chain and <Code>GET /api/audit/verify</Code> names the first row that fails.
        </Lede>
        <div className="mt-10 grid gap-3.5 sm:grid-cols-3">
          {[
            ["Chained", "SHA-256 per row", "Each row hashes its own content together with its predecessor's hash."],
            ["Verifiable", "One endpoint", "Recomputes the chain and returns the first trace ID that does not match."],
            ["Complete", "Every decision", "Allowed responses are logged too. A log of only the refusals cannot show a false negative."],
          ].map(([k, h, p], i) => (
            <Reveal key={k} delay={i * 0.05}>
              <Panel className="h-full">
                <p className="label text-mute">{k}</p>
                <h3 className="mt-3 font-display text-lg">{h}</h3>
                <p className="mt-2 text-sm text-ink-2">{p}</p>
              </Panel>
            </Reveal>
          ))}
        </div>
        <Reveal>
          <Callout>
            <b className="font-semibold text-ink">What this is not.</b> SQLite cannot prevent an UPDATE
            or a DELETE, so the guarantee is tamper-<i>evidence</i>, not immutability. Mid-chain edits,
            tail truncation and forged review verdicts all break verification — a review is an appended
            chained row, not a mutable column. The remaining ceiling is that the chain head lives in the
            file it protects; anchoring it externally is the upgrade.
          </Callout>
        </Reveal>
      </Section>

      {/* ── Measured ── */}
      <Section id="measured">
        <Eyebrow>Measured</Eyebrow>
        <SectionTitle>Numbers, including the ones that don&rsquo;t flatter it.</SectionTitle>
        <Lede>
          A governance tool that publishes only its good numbers is asking you to trust the thing it was
          built to remove. These come from running the system, not from a slide.
        </Lede>
        <div className="mt-10 grid gap-3.5 sm:grid-cols-2 lg:grid-cols-4">
          {[
            // Generated by scripts/export_metrics.py from an actual eval run. Typed
            // in by hand these drift, and a stale accuracy figure is precisely the
            // failure this project exists to catch.
            { v: <Counter to={measured.decisionStates} />, l: "Decision states, all reachable" },
            { v: <Counter to={measured.tests} />, l: "Tests, none of them mocking what they test" },
            { v: <Counter to={measured.evalCases} />, l: "Labelled eval cases, split dev, held out and unseen" },
            { v: worstUnseenRecall.toFixed(2), l: `Worst recall on unseen phrasing (${worstUnseenName})` },
          ].map((s, i) => (
            <Reveal key={s.l} delay={i * 0.05}>
              <div className="h-full border border-rule bg-card p-6">
                <div className="font-display text-[clamp(30px,3.6vw,40px)] leading-none tracking-[-0.015em]">{s.v}</div>
                <div className="mt-2.5 text-[13.5px] text-mute">{s.l}</div>
              </div>
            </Reveal>
          ))}
        </div>
        <div className="mt-3.5 overflow-x-auto border border-rule bg-card">
          <table className="w-full border-collapse text-[13.5px]">
            <thead>
              <tr className="border-b border-rule text-left label text-mute">
                <th className="px-5 py-3 font-normal">Detector</th>
                <th className="px-5 py-3 text-right font-normal">Held-out recall</th>
                <th className="px-5 py-3 text-right font-normal">Unseen phrasing</th>
                <th className="px-5 py-3 text-right font-normal">Gap</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {RECALL_ROWS.map((r) => (
                <tr key={r.name} className="border-b border-rule/60 last:border-0">
                  <td className="px-5 py-3 font-sans">{r.name}</td>
                  <td className="px-5 py-3 text-right">{r.heldOut.toFixed(2)}</td>
                  <td className="px-5 py-3 text-right">{r.unseen.toFixed(2)}</td>
                  <td className={cn("px-5 py-3 text-right", r.gap < -0.2 ? "text-block" : "text-mute")}>
                    {r.gap >= 0 ? "+" : ""}{r.gap.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Reveal>
          <Callout>
            <b className="font-semibold text-ink">The right-hand column is the honest one.</b> Both sets are
            held out, but the first was written alongside the patterns, so it measures whether they still
            work rather than whether they generalise. The unseen set is written to deliberately different
            phrasings and was never used to adjust anything — and recall on it falls by two thirds for bias
            and injection. That is what pattern matching does: it catches the wordings it was written for
            and misses paraphrase. Privacy degrades least, because PII has real structure — checksums and
            formats — rather than vocabulary. Closing that gap means a classifier, not more regexes. Every
            known miss is recorded in <Code>evals/gates.json</Code> rather than tuned away.
          </Callout>
        </Reveal>
      </Section>

      {/* ── CTA ── */}
      <Section tint>
        <div className="text-center">
          <Reveal>
            <h2 className="mx-auto font-display text-[clamp(28px,4vw,44px)] leading-[1.12] tracking-[-0.015em]">
              Run it against your own text.
            </h2>
          </Reveal>
          <Reveal delay={0.05}>
            <p className="mx-auto mt-3.5 max-w-[62ch] text-[17px] text-ink-2">
              The console posts to the same <Code>/api/evaluate</Code> this page uses, and shows the trace it wrote.
            </p>
          </Reveal>
          <Reveal delay={0.1}>
            <div className="mt-7 flex flex-wrap justify-center gap-3">
              <a href="/console.html" className="inline-flex h-11 items-center border border-block bg-block px-6 label text-paper transition hover:bg-ink hover:border-ink">
                Open the console
              </a>
              <a href={`${API_BASE}/docs`} className="inline-flex h-11 items-center border border-ink px-6 label transition hover:bg-ink hover:text-paper">
                Browse the API
              </a>
            </div>
          </Reveal>
        </div>
      </Section>

      {/* ── Footer ── */}
      <footer className="border-t border-ink bg-card py-10">
        <div className="spectrum mx-auto mb-7 h-[3px] w-full max-w-[1120px]" />
        <div className="mx-auto flex w-full max-w-[1120px] flex-wrap items-center gap-4 px-5 sm:px-12">
          <Brand />
          <div className="flex-1" />
          <span className="label text-mute">Runtime control plane · prototype, not a production deployment</span>
        </div>
      </footer>
    </>
  );
}

/* ── small building blocks ── */

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2.5">
      <span className="relative size-5.5 shrink-0 bg-ink">
        <span className="spectrum absolute inset-x-1 top-[9.5px] h-[3px]" />
      </span>
      <span className="font-display text-lg tracking-[-0.01em]">Aether</span>
    </Link>
  );
}

function Section({ id, tint, children }: { id?: string; tint?: boolean; children: React.ReactNode }) {
  return (
    <section id={id} className={cn("relative py-16 sm:py-24", tint && "border-y border-rule bg-card")}>
      <div className="mx-auto w-full max-w-[1120px] px-5 sm:px-12">{children}</div>
    </section>
  );
}

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <Reveal>
      <p className="mb-5 flex items-center gap-3 label text-block">
        <span className="h-px w-7 shrink-0 bg-block" />
        {children}
      </p>
    </Reveal>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <Reveal>
      <h2 className="max-w-[20ch] font-display text-[clamp(28px,4vw,44px)] leading-[1.12] tracking-[-0.015em]">
        {children}
      </h2>
    </Reveal>
  );
}

function Lede({ children }: { children: React.ReactNode }) {
  return (
    <Reveal delay={0.05}>
      <p className="mt-3.5 max-w-[62ch] text-[17px] text-ink-2">{children}</p>
    </Reveal>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="border border-rule bg-sunk px-1.5 py-px font-mono text-[0.85em]">
      {children}
    </code>
  );
}

function Callout({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-7 border-y border-r border-l-2 border-rule border-l-block bg-sunk px-5 py-4 text-[14.5px] text-ink-2">
      {children}
    </p>
  );
}
