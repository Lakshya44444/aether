import type { Metadata } from "next";
import { DM_Serif_Text, Syne, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const dmSerif = DM_Serif_Text({
  variable: "--font-dm-serif",
  subsets: ["latin"],
  weight: ["400"],
});
const syne = Syne({
  variable: "--font-syne",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});
const jetbrains = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
  weight: ["400", "500", "700"],
});

export const metadata: Metadata = {
  title: "Sentinel — govern the action, not just the answer",
  description:
    "A runtime control plane for enterprise AI. Sentinel scores the model's output, weighs it against the action the AI is about to take, and returns one of five decisions with a tamper-evident audit trail.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // The font variables go on <html>, not <body>. @theme resolves --font-display at
    // :root, so a variable declared one level lower is out of scope there and the whole
    // family silently falls back to the system stack.
    <html lang="en" className={`${dmSerif.variable} ${syne.variable} ${jetbrains.variable}`}>
      <body>{children}</body>
    </html>
  );
}
