import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: [
    "127.0.0.1",
    "localhost",
    "http://127.0.0.1:5001",
    "http://localhost:5001",
  ],
};

export default nextConfig;
