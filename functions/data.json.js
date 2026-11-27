export async function onRequest(context) {
  // cacheTtl lets Cloudflare edge-cache the KV value; the response headers below
  // let the CDN serve /data.json itself, so a page view is not automatically a
  // function invocation + a KV read (free plan caps both at 100k/day).
  const data = await context.env.peace_data.get("data.json", { cacheTtl: 60 });
  if (!data) {
    return new Response("Data not available", {
      status: 503,
      headers: { "Cache-Control": "no-store" },
    });
  }
  return new Response(data, {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      // The pipeline publishes ~10 times a day — browsers revalidate quickly,
      // the CDN serves the bulk of the traffic.
      "Cache-Control": "public, max-age=0, s-maxage=120, stale-while-revalidate=600",
      "CF-CDN-Cache-Control": "public, max-age=120, stale-while-revalidate=600",
      "Vary": "Accept-Encoding",
    },
  });
}
