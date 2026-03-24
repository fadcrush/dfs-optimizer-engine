/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  allowedDevOrigins: ['127.0.0.1', 'localhost'],
  // No remotePatterns configured — mitigates GHSA-9g9p-9gw9-jx7f (image optimizer DoS)
  // Do NOT add remotePatterns without validating trusted origins.
  images: {
    remotePatterns: [],
  },
}

module.exports = nextConfig
