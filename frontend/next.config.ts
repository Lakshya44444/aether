import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export so the FastAPI gateway can serve the built frontend directly
  // from its existing StaticFiles mount — no second server in production.
  output: "export",
  images: { unoptimized: true },
};

export default nextConfig;
