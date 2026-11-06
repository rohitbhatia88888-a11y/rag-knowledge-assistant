# Folio Copilot Studio agent: build checklist

Goal: a "Folio Helpdesk" agent in Copilot Studio that
- answers from a **Dataverse** knowledge table (FAQs)
- calls **Folio** through a **custom connector** for document questions
- runs one **Power Automate agent flow** that logs every escalation to Dataverse and asks Folio

Budget: roughly 2 to 3 evenings. Start the Copilot Studio trial only once step 0 is done, so the trial clock covers the build.

---

## 0. Prep Folio (before any trial starts)

- [ ] **Add a non-streaming endpoint.** `POST /chat` returns Server-Sent Events, and a connector can't read those. Add `POST /chat/answer` to `backend/app/api/chat.py`. It takes the same `ChatRequest`, drains `_answer_stream` (or factors the logic out), and returns JSON:
  `{session_id, answer, idk, sources[], latency_ms}` (`sources` without `text`, to keep the payload small).
- [ ] **Sort out a token that lasts.** Tokens are JWTs with `exp = session_ttl_hours`. For the demo, mint one for a demo persona with a longer TTL, or add a small service-token path. Be ready to explain in interview why you'd move to OAuth (Entra ID) in production.
- [ ] **Deploy** to the Oracle box (`130-61-18-125.sslip.io`) and check that `curl -H "Authorization: Bearer …" https://…/chat/answer` returns JSON.

## 1. Environment

- [ ] Sign up for the **Power Apps Developer Plan** (free, gives a personal environment with Dataverse).
- [ ] Start a **Copilot Studio trial** and select that developer environment.

## 2. Custom connector

- [ ] make.powerapps.com → Custom connectors → New → **Import an OpenAPI file** → `folio-connector.swagger.yaml`.
- [ ] Security tab: API Key, header `Authorization`. When you create the connection, paste `Bearer <token>`.
- [ ] Test tab: run `WhoAmI`, then `AskFolio` with a sample question.
- [ ] Screenshot the successful test. It's good material for the README.

## 3. Dataverse knowledge

- [ ] Create a table `Helpdesk FAQ` with columns Question, Answer and Category, and add 15 to 20 rows.
- [ ] Create a table `Escalation` with columns Question, User, Folio answer, Sources, Created on and Status.
- [ ] In the agent, open Knowledge → Add → Dataverse → `Helpdesk FAQ`.

## 4. Agent flow

- [ ] Create a flow with the trigger "When an agent calls the flow". Inputs: `question` and `user`.
- [ ] Add a step that runs **AskFolio** through the connector.
- [ ] Add a step that does Dataverse **Add a row** to `Escalation` with the Folio answer and source titles.
- [ ] Add a "Respond to the agent" step that returns `answer` and `escalation_id`.

## 5. Agent

- [ ] Instructions: answer from FAQ knowledge first. For document or policy questions, use the Folio tool. If the user asks for a human, run the escalation flow.
- [ ] Add tools: the AskFolio connector action, plus the agent flow.
- [ ] Test three paths: an FAQ hit, a Folio answer with citations, and an escalation that writes a row.
- [ ] Publish to the demo website channel.

## 6. Interview kit

- [ ] README with an architecture diagram: user → Copilot Studio agent → (Dataverse knowledge | Folio connector | agent flow → Dataverse + Folio).
- [ ] A 60-second recording of all three paths.
- [ ] Talking points:
  - Why you added a non-streaming endpoint.
  - How auth works (API-key header now, Entra ID OAuth for production).
  - How Folio's ACL filtering keeps permissions intact behind the agent.
  - What you'd do next (OAuth per user, so each user gets their own document permissions).
- [ ] Record everything **before the trial expires**.
