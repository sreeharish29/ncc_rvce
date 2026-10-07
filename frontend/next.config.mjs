const isExport = process.env.NEXT_EXPORT === "1";

/** @type {import('next').NextConfig} */
const nextConfig = {
  ...(isExport ? { output: "export" } : {}),
  images: { unoptimized: true },
  ...(!isExport && {
    async rewrites() {
      return [{ source: "/api/:path*", destination: "http://localhost:8000/api/:path*" }];
    },
  }),
};

export default nextConfig;