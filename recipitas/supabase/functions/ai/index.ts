// Recipe Box AI function: reads recipes (voice, photo, file, text) and makes changes by voice.
// Your Gemini key lives only in this function's secrets (GEMINI_API_KEY); the app and its visitors never see it.
import mammoth from "npm:mammoth@1.13.0";
import { Buffer } from "node:buffer";

const GEMINI_MODEL = Deno.env.get("GEMINI_MODEL") || "gemini-2.5-flash";
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-family-code",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
const str = (v: unknown) => (v === null || v === undefined ? "" : String(v));

const RECIPE_SCHEMA = {
  type: 'OBJECT',
  properties: {
    found_recipe: { type: 'BOOLEAN', description: 'false if the input contains no recipe at all' },
    transcript: { type: 'STRING', description: 'For audio input: a faithful transcript of what was said. Empty otherwise.' },
    name: { type: 'STRING' },
    description: { type: 'STRING' },
    prep_time: { type: 'STRING' },
    cook_time: { type: 'STRING' },
    total_time: { type: 'STRING' },
    servings: { type: 'STRING' },
    category: { type: 'STRING' },
    notes: { type: 'STRING' },
    source: { type: 'STRING' },
    ingredients: {
      type: 'ARRAY',
      items: {
        type: 'OBJECT',
        properties: {
          quantity: { type: 'STRING', description: 'Amount exactly as given: "1/2", "a small", "2-3", "enough". Empty if none.' },
          unit: { type: 'STRING', description: 'Unit exactly as given: "cup", "handful", "tidbit", "can". Empty if none.' },
          name: { type: 'STRING', description: 'The ingredient itself, e.g. "fresh thyme", "water to cover the potatoes", "salt to taste".' },
          prep_notes: { type: 'STRING', description: 'Preparation such as "chopped", "softened". Empty if none.' },
        },
        required: ['quantity', 'unit', 'name', 'prep_notes'],
      },
    },
    steps: { type: 'ARRAY', items: { type: 'STRING' } },
  },
  required: ['found_recipe', 'transcript', 'name', 'description', 'prep_time', 'cook_time', 'total_time', 'servings', 'category', 'notes', 'source', 'ingredients', 'steps'],
};

const EXTRACT_PROMPT = `You turn a recipe into structured fields for a personal recipe box.

Rules:
- Use the cook's own wording. Never convert, round, or standardize measurements. "half a cup" stays quantity "half a", unit "cup". "a tidbit of butter" is quantity "a", unit "tidbit", name "butter". "2 handfuls" is quantity "2", unit "handfuls".
- Not every ingredient has a number. "Salt to taste" -> quantity "", unit "", name "salt to taste". "Enough water to cover the potatoes" -> quantity "enough", unit "", name "water to cover the potatoes".
- Keep preparation words ("chopped", "melted", "room temperature") in prep_notes.
- Never invent information. Leave any field empty ("") if it was not stated. Do not guess times, servings or categories.
- category: only fill if the cook names one or it is unmistakable (e.g. "dessert", "soup"); otherwise "".
- steps: one entry per instruction step, in order, in the cook's words lightly cleaned of filler ("um", "uh", false starts).
- For speech: the cook may correct themselves ("two cups, no, three cups") — use the correction. Ignore talk that is not part of the recipe.
- For photos: read handwriting and print carefully; if several images are given they are pages of the same recipe, in order.
- If there is no recipe at all, set found_recipe to false and leave the rest empty.`;

const RECIPE_FIELDS_SCHEMA = Object.fromEntries(Object.entries(RECIPE_SCHEMA.properties).filter(([k]) => k !== 'found_recipe' && k !== 'transcript')) as Record<string, unknown>;
const EDIT_SCHEMA = {
  type: 'OBJECT',
  properties: {
    understood: { type: 'BOOLEAN', description: 'false if the request is unclear or is not a change to this recipe' },
    transcript: { type: 'STRING', description: 'For audio: what the cook said. Empty otherwise.' },
    summary: { type: 'STRING', description: 'One short sentence describing exactly what was changed, or what was unclear.' },
    recipe: { type: 'OBJECT', properties: RECIPE_FIELDS_SCHEMA, required: Object.keys(RECIPE_FIELDS_SCHEMA) },
  },
  required: ['understood', 'transcript', 'summary', 'recipe'],
};
const EDIT_PROMPT = `You make one requested change to a cook's saved recipe.

You get the current recipe as JSON and the cook's request (spoken or typed). Return the whole recipe with the change applied.

Rules:
- Change ONLY what the cook asked for. Copy every other field, ingredient and step exactly, character for character, in the same order.
- New or changed amounts use the cook's own words. Never convert units or standardize ("a splash more" stays wording like that).
- The cook may refer to things loosely: "the butter", "step three", "the last step", "the onions". Match them to the right line.
- They may add, remove, reword or reorder ingredients or steps, rename the recipe, or set times, servings, category, notes, source or description.
- Only rescale amounts if they ask to (e.g. "double it"); then change only quantities, written in the same style.
- Spoken requests may include filler or self-corrections; use the final intent.
- If the request is unclear, not about this recipe, or would mean guessing, set understood to false, explain briefly in summary, and return the recipe unchanged.
- summary: one short plain sentence, e.g. "Changed butter to 2 tablespoons and added a resting step at the end."`;


async function callGemini(model: string, parts: unknown[], prompt: string, schema: unknown) {
  const key = Deno.env.get("GEMINI_API_KEY");
  const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-goog-api-key": key! },
    body: JSON.stringify({
      systemInstruction: { parts: [{ text: prompt }] },
      contents: [{ role: "user", parts }],
      generationConfig: { responseMimeType: "application/json", responseSchema: schema, temperature: 0.1 },
    }),
  });
  const text = await r.text();
  let body: any = null; try { body = JSON.parse(text); } catch { /* not json */ }
  return { status: r.status, body, text };
}
async function ask(parts: unknown[], prompt: string, schema: unknown) {
  let out = await callGemini(GEMINI_MODEL, parts, prompt, schema);
  if (out.status === 404 && GEMINI_MODEL !== "gemini-flash-latest") out = await callGemini("gemini-flash-latest", parts, prompt, schema);
  if (out.status !== 200) throw new Error("Gemini error: " + ((out.body && out.body.error && out.body.error.message) || out.text.slice(0, 300)));
  const cand = out.body.candidates && out.body.candidates[0];
  const txt = cand && cand.content && cand.content.parts && cand.content.parts.map((p: any) => p.text || "").join("");
  if (!txt) throw new Error("Gemini returned nothing" + (cand && cand.finishReason ? " (" + cand.finishReason + ")" : ""));
  return JSON.parse(txt);
}

async function documentParts(f: any) {
  const name = str(f.name).toLowerCase(), mime = str(f.mimeType);
  const buf = Buffer.from(f.data, "base64");
  if (mime === "application/pdf" || name.endsWith(".pdf")) return [{ inlineData: { mimeType: "application/pdf", data: f.data } }];
  if (/^image\//.test(mime)) return [{ inlineData: { mimeType: mime, data: f.data } }];
  if (name.endsWith(".docx") || mime === "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    return [{ text: "Recipe from a Word document:\n\n" + (await mammoth.extractRawText({ buffer: buf })).value }];
  if (/\.(html?)$/.test(name) || mime === "text/html")
    return [{ text: "Recipe from a web page:\n\n" + buf.toString("utf8").replace(/<(script|style)[\s\S]*?<\/\1>/gi, "").replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&") }];
  if (name.endsWith(".rtf")) return [{ text: "Recipe text:\n\n" + buf.toString("latin1").replace(/\\par[d]?/g, "\n").replace(/\{\*?\\[^{}]+}|[{}]|\\[A-Za-z]+-?\d* ?/g, "") }];
  if (/^text\//.test(mime) || /\.(txt|md|csv)$/.test(name)) return [{ text: "Recipe text:\n\n" + buf.toString("utf8") }];
  return null;
}

// Asks the database whether this family code is right (the database holds only a scrambled copy).
async function familyOk(code: string) {
  if (!code.trim()) return false;
  let key = Deno.env.get("SUPABASE_ANON_KEY") || "";
  if (!key) { try { const k = JSON.parse(Deno.env.get("SUPABASE_PUBLISHABLE_KEYS") || "{}"); key = String(Object.values(k)[0] || ""); } catch { /* none */ } }
  const r = await fetch(Deno.env.get("SUPABASE_URL") + "/rest/v1/rpc/family_ok", {
    method: "POST",
    headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json", "x-family-code": code },
    body: "{}",
  });
  if (!r.ok) { console.error("family check failed", r.status, await r.text()); return false; }
  return (await r.json()) === true;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json(405, { error: "POST only" });

  // Turn AI off any time by setting the AI_ENABLED secret to "off" (or deleting GEMINI_API_KEY).
  if ((Deno.env.get("AI_ENABLED") || "on").toLowerCase() === "off") return json(503, { error: "Recipe reading is turned off right now." });
  // Only someone with the family code may use the Gemini key.
  if (!(await familyOk(req.headers.get("x-family-code") || ""))) return json(401, { error: "Wrong or missing family code." });
  if (!Deno.env.get("GEMINI_API_KEY")) return json(503, { error: "Gemini is not set up yet. Add GEMINI_API_KEY in Supabase: Edge Functions, Secrets." });

  const len = Number(req.headers.get("content-length") || 0);
  if (len > 28 * 1024 * 1024) return json(413, { error: "That file is too big." });
  let b: any;
  try { b = await req.json(); } catch { return json(400, { error: "Bad request" }); }
  try {
    if (b.action === "edit") {
      const rec = b.recipe;
      if (!rec || typeof rec !== "object") return json(400, { error: "No recipe to change" });
      const current: Record<string, unknown> = {};
      for (const k of Object.keys(RECIPE_FIELDS_SCHEMA)) {
        if (k === "ingredients") current.ingredients = (Array.isArray(rec.ingredients) ? rec.ingredients : []).map((i: any) => ({ quantity: str(i.quantity), unit: str(i.unit), name: str(i.name), prep_notes: str(i.prep_notes) }));
        else if (k === "steps") current.steps = (Array.isArray(rec.steps) ? rec.steps : []).map((x: any) => str(typeof x === "string" ? x : x && x.text));
        else current[k] = str(rec[k]);
      }
      const parts: unknown[] = [{ text: "Current recipe:\n" + JSON.stringify(current, null, 1) }];
      const f = Array.isArray(b.files) && b.files[0];
      if (f) {
        if (!/^audio\//.test(str(f.mimeType)) || !f.data) return json(400, { error: "Unsupported file type" });
        parts.push({ inlineData: { mimeType: f.mimeType, data: f.data } });
        parts.push({ text: 'The recording is the cook asking for a change. Put what they said in "transcript".' });
      } else if (str(b.text).trim()) parts.push({ text: "Requested change:\n" + str(b.text) });
      else return json(400, { error: "Say or type the change you want" });
      const r = await ask(parts, EDIT_PROMPT, EDIT_SCHEMA);
      if (!r.understood) return json(422, { error: r.summary || "That change was not clear.", transcript: str(r.transcript) });
      return json(200, { summary: str(r.summary), transcript: str(r.transcript), recipe: r.recipe });
    }

    // action: extract
    const kind = b.kind, files = Array.isArray(b.files) ? b.files : [];
    const parts: any[] = [];
    if (kind === "text") {
      if (!str(b.text).trim()) return json(400, { error: "No text to read" });
      parts.push({ text: "Recipe text:\n\n" + str(b.text) });
    } else if (kind === "audio" || kind === "image") {
      if (!files.length) return json(400, { error: "No file received" });
      const okType = kind === "audio" ? /^audio\// : /^image\//;
      for (const f of files) { if (!okType.test(str(f.mimeType)) || !f.data) return json(400, { error: "Unsupported file type" }); parts.push({ inlineData: { mimeType: f.mimeType, data: f.data } }); }
      parts.push({ text: kind === "audio" ? 'This is a recording of someone speaking a recipe. Transcribe it into "transcript", then fill in the recipe fields.' : "These are photos of a recipe. Fill in the recipe fields." });
    } else if (kind === "document") {
      const p = files[0] && files[0].data ? await documentParts(files[0]) : null;
      if (!p) return json(415, { error: "That kind of file isn't supported. Try a PDF, Word (.docx), text file or photo." });
      parts.push(...p, { text: "Fill in the recipe fields from this file. If it holds several recipes, use the first one." });
    } else return json(400, { error: "Unknown input kind" });
    const r = await ask(parts, EXTRACT_PROMPT, RECIPE_SCHEMA);
    if (r.found_recipe === false) return json(422, { error: "No recipe was found in that input.", transcript: str(r.transcript) });
    return json(200, r);
  } catch (e) {
    console.error(e);
    return json(502, { error: e instanceof Error ? e.message : "Something went wrong" });
  }
});
