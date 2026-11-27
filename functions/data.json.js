export async function onRequest(context) {
  // KV cacheTtl edge-caches the KV *value*, so a page view is not necessarily an
  // origin KV read. Note (measured with a probe, 2026-11-27): the CDN does NOT
  // serve this response from cache on this plan — Cache-Control / CF-CDN-Cache-Control
  // are echoed but a function invocation still happens per request. Do not add a
  // `no-store` here anyway; the headers are harmless and become effective if the
  // plan gains Cache Rules.
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
      // The pipeline publishes ~10 times a day. Browsers revalidate quickly and
      // forward the same headers to any intermediary in front of them (mobile
      // carriers, corporate proxies) that does honour Cache-Control.
      "Cache-Control": "public, max-age=0, s-maxage=120, stale-while-revalidate=600",
      "CF-CDN-Cache-Control": "public, max-age=120, stale-while-revalidate=600",
      "Vary": "Accept-Encoding",
    },
  });
}
