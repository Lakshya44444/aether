"use client";

import { motion, useInView, useScroll, useSpring, useTransform } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

/* Scroll reveal. One motion vocabulary for the whole page — scattered effects
   are what make a design feel generated. */
export function Reveal({ children, className, delay = 0 }: { children: React.ReactNode; className?: string; delay?: number }) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.6, delay, ease: [0.22, 0.68, 0.28, 1] }}
    >
      {children}
    </motion.div>
  );
}

/* A panel: hairline rule, flat ground, no lift.

   This was a card that tracked the cursor and painted a blue radial glow under it,
   floated on hover, and carried a soft drop shadow. None of that told the reader
   anything about the content, and all three together are the house style of a
   generated dashboard. What is left is a ruled box, which is what it always was. */
export function Panel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("relative border border-rule bg-card p-6", className)}>{children}</div>
  );
}

/* Aceternity's Tracing Beam, simplified: the beam fills with scroll progress
   through the section, and is coloured by the decision ladder. */
export function TracingBeam({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start 0.6", "end 0.7"] });
  const progress = useSpring(scrollYProgress, { stiffness: 220, damping: 40 });
  // Reveal the gradient by clipping it, rather than scaling it. scaleY squashes the
  // five colour stops into whatever height is currently shown, which blends them into
  // one muddy brown instead of showing the ladder.
  const clipPath = useTransform(progress, (p) => `inset(0 0 ${(1 - p) * 100}% 0)`);

  return (
    <div ref={ref} className="relative pl-12">
      <div className="absolute bottom-1.5 left-[15px] top-1.5 w-0.5 overflow-hidden bg-rule">
        <motion.div className="spectrum-v absolute inset-0" style={{ clipPath }} />
      </div>
      {children}
    </div>
  );
}

/* Count-up when the number scrolls into view. */
export function Counter({ to, className }: { to: number; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-60px" });
  const [n, setN] = useState(0);

  useEffect(() => {
    if (!inView) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return setN(to);
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / 900);
      setN(Math.round(to * (1 - Math.pow(1 - p, 3))));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [inView, to]);

  return <span ref={ref} className={className}>{n}</span>;
}
