export async function onRequest(context) {
  const data = await context.env.peace_data.get("data.json");
  if (!data) {
    return new Response("Data not available", { status: 503 });
  }
  return new Response(data, {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "public, max-age=60",
    },
  });
}
