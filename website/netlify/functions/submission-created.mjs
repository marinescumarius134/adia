// Runs on Netlify after every form submission that passed the spam filter (the file name is the trigger).
// Each submission stays in Netlify Forms (the raw record, with the message-test fields). This function
// also asks Brevo to send its double opt-in email: the address joins the Brevo list(s) only after the
// person clicks the confirmation link.
//
// Environment variables (Netlify > Site configuration > Environment variables):
//   BREVO_API_KEY                 Brevo > SMTP & API > API keys
//   BREVO_DOI_TEMPLATE_ID         the confirmation email template (see website/email/README.md)
//   BREVO_LIST_WAITLIST           list id: launch news
//   BREVO_LIST_SMOG_BUCURESTI     list id: smog alerts, Bucharest
//   SITE_URL (optional)           defaults to Netlify's own URL variable
// Without BREVO_API_KEY nothing is sent to Brevo, and the submissions are only kept in Netlify.

const API = "https://api.brevo.com/v3/contacts/doubleOptinConfirmation";
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export async function handler(event) {
  const ok = { statusCode: 200, body: "" };
  let payload;
  try {
    payload = JSON.parse(event.body || "{}").payload || {};
  } catch {
    console.error("submission-created: body isn't JSON");
    return ok;
  }
  const form = payload.form_name;
  const d = payload.data || {};
  if (form !== "lista" && form !== "alerta") return ok;          // "pret" is research only, never mailed

  const env = process.env;
  if (!env.BREVO_API_KEY) {
    console.log(`submission-created: BREVO_API_KEY not set, "${form}" kept in Netlify Forms only`);
    return ok;
  }
  const email = String(d.email || "").trim().toLowerCase();
  if (!EMAIL.test(email) || d["bot-field"] || d.consent !== "da") {
    console.log(`submission-created: "${form}" skipped (invalid email, honeypot or no consent)`);
    return ok;
  }

  const cityList = env.BREVO_LIST_SMOG_BUCURESTI;
  const wanted = form === "lista"
    ? [env.BREVO_LIST_WAITLIST, d.alerta === "da" ? cityList : null]
    : [cityList, d.lista === "da" ? env.BREVO_LIST_WAITLIST : null];
  const lists = [...new Set(wanted.map(Number).filter(n => Number.isInteger(n) && n > 0))];
  const templateId = Number(env.BREVO_DOI_TEMPLATE_ID);
  const site = String(env.SITE_URL || env.URL || "").replace(/\/+$/, "");
  if (!lists.length || !templateId || !site) {
    console.error("submission-created: set BREVO_DOI_TEMPLATE_ID, the BREVO_LIST_* ids and SITE_URL");
    return ok;
  }

  const res = await fetch(API, {
    method: "POST",
    headers: { "api-key": env.BREVO_API_KEY, "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({
      email,
      includeListIds: lists,
      templateId,
      redirectionUrl: `${site}/?confirmat=1#${d.limba === "ro" ? "ro" : "en"}`,   // the site opens in English unless #ro
    }),
  }).catch(err => ({ ok: false, status: 0, text: async () => String(err) }));
  if (res.ok) console.log(`submission-created: "${form}" -> Brevo double opt-in sent, lists ${lists.join(",")}`);
  else console.error(`submission-created: Brevo answered ${res.status}: ${(await res.text()).slice(0, 300)}`);
  return ok;
}
