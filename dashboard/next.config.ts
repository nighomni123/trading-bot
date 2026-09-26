import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Artifact JSON lives outside the app dir (../docs, ../research).
  serverExternalPackages: ["echarts"],
};

export default nextConfig;
