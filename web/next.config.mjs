/** @type {import('next').NextConfig} */
// NEXT_OUTPUT=export builds a static site that the API serves itself (the single-container demo).
const nextConfig = { output: process.env.NEXT_OUTPUT === "export" ? "export" : "standalone" };

export default nextConfig;
