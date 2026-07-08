# AI PROMPT — PRODUCTION

## INPUT

{
  "status": "Review",
  "score": 5,
  "confidence": "high",
  "reasons": [
    "Average time increased 42%",
    "WIP increased 30%",
    "Throughput decreased 25%"
  ]
}

---

## PROMPT TEMPLATE

"You are a system that explains software delivery bottlenecks.

Given the structured data below, generate a concise explanation.

Rules:
- Do not invent numbers
- Do not change meaning
- Be clear and actionable

Data:
{input}

Output:"

---

## OUTPUT

"Review is currently the main bottleneck. Work is spending significantly more time in this stage, the queue is growing, and fewer items are being completed."