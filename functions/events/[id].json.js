// Serves one solution's full event history from KV (key: events/<id>.json), so
// data.json only has to carry the events the first screen needs (it used to carry
// all of them: 110 KB, 58.8% of the payload, for a feed that shows four).
//
// Written by ai-analyze-prod.py alongside data.json; the frontend only asks for
// these when data.eventsFiles is present.
const ID_RE = /^[a-z0-9][a-z0-9-]{0,79}$/;

const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "public, max-age=0, s-maxage=120, stale-while-revalidate=600",
  "CF-CDN-Cache-Control": "public, max-age=120, stale-while-revalidate=600",
  "Vary": "Accept-Encoding",
};

function answer(status, body, extra = {}) {
  return new Response(body, { status, headers: { ...JSON_HEADERS, ...extra } });
}

export async function onRequest(context) {
  const id = context.params.id || "";

  // Never let an arbitrary string reach KV — a wildcard-ish id would turn the
  // endpoint into a KV key prober.
  if (!ID_RE.test(id)) {
    return answer(400, JSON.stringify({ error: "bad solution id" }));
  }

  const data = await context.env.peace_data.get(`events/${id}.json`, { cacheTtl: 60 });
  if (!data) {
    // Short-lived on purpose: the next pipeline run may well create this file.
    return answer(404, JSON.stringify({ error: "no event archive for this solution", solutionId: id }),
      { "Cache-Control": "public, max-age=0, s-maxage=30" });
  }
  return answer(200, data);
}
