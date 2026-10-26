// Plain-language names for the score letters (SPEC section 3), shared by cards, tables and the report.
export const SCORE_INFO = {
  final: { name: "Final score", hint: "Overall ranking score (0 to 1): track record, interest, fit and confidence combined, adjusted for location and the AI judge." },
  t: { name: "Track record", hint: "T: how proven the founders are, from verified exits, VC-backed companies, top investors, scale and years in education." },
  i: { name: "Interesting", hint: "I: momentum (recent funding, hiring, news) plus novelty (a fresh idea in a growing theme)." },
  m: { name: "Momentum", hint: "M: recent funding, hiring, news and GitHub traction; older signals count less." },
  n: { name: "Novelty", hint: "N: how far the idea is from crowded clusters, times how fast its theme is growing." },
  f: { name: "Fit with upGrad", hint: "F: how well the company matches upGrad's business units and wanted capabilities." },
  c: { name: "Confidence", hint: "C: share of the expected facts that are verified against a source. Low = thin evidence." },
} as const;

export type ScoreKey = keyof typeof SCORE_INFO;
