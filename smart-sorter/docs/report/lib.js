// Tiện ích tạo báo cáo Word bằng docx-js (dùng bởi build_report.js)
const docx = require("docx");
const {
  Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType, AlignmentType,
  ShadingType, BorderStyle, PageBreak, LevelFormat, TableOfContents, ImageRun,
} = docx;

const FONT = "Times New Roman";
const CODE_FONT = "Consolas";
const PAGE_W = 11906; // A4 DXA
const MARGIN = 1134;  // 2 cm
const CONTENT_W = PAGE_W - 2 * MARGIN;

function h1(text) { return new Paragraph({ text, heading: HeadingLevel.HEADING_1, spacing: { before: 360, after: 160 } }); }
function h2(text) { return new Paragraph({ text, heading: HeadingLevel.HEADING_2, spacing: { before: 280, after: 120 } }); }
function h3(text) { return new Paragraph({ text, heading: HeadingLevel.HEADING_3, spacing: { before: 200, after: 80 } }); }

function runs(text, opts = {}) {
  // hỗ trợ **đậm** và `code` inline
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), font: FONT, size: 26, ...opts }));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(new TextRun({ text: tok.slice(2, -2), bold: true, font: FONT, size: 26, ...opts }));
    else out.push(new TextRun({ text: tok.slice(1, -1), font: CODE_FONT, size: 22, ...opts }));
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), font: FONT, size: 26, ...opts }));
  return out;
}

function p(text, opts = {}) {
  return new Paragraph({ children: runs(text, opts.run || {}), alignment: AlignmentType.JUSTIFIED, spacing: { after: 120, line: 300 }, ...(opts.para || {}) });
}
function note(text) {
  return new Paragraph({ children: runs(text, { italics: true, color: "444444" }), spacing: { after: 120 }, indent: { left: 400 } });
}
function bullet(text, level = 0) {
  return new Paragraph({ children: runs(text), numbering: { reference: "bullets", level }, spacing: { after: 60, line: 280 } });
}
function numbered(text, level = 0) {
  return new Paragraph({ children: runs(text), numbering: { reference: "numbers", level }, spacing: { after: 60, line: 280 } });
}
function code(lines) {
  const arr = Array.isArray(lines) ? lines : String(lines).split("\n");
  return arr.map((ln) => new Paragraph({
    children: [new TextRun({ text: ln.length ? ln : " ", font: CODE_FONT, size: 18 })],
    shading: { type: ShadingType.CLEAR, fill: "F2F2F2", color: "auto" },
    spacing: { after: 0, line: 240 },
    indent: { left: 200 },
  }));
}

function cell(text, { header = false, width, fill, align } = {}) {
  const lines = String(text).split("\n");
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    shading: fill ? { type: ShadingType.CLEAR, fill, color: "auto" } : undefined,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    verticalAlign: docx.VerticalAlign.CENTER,
    children: lines.map((ln) => new Paragraph({
      children: runs(ln, { bold: header, size: header ? 22 : 22 }),
      alignment: align || AlignmentType.LEFT,
      spacing: { after: 0, line: 260 },
    })),
  });
}

/** table(headers, rows, widthsRatio) — widthsRatio là mảng tỉ lệ cột (tổng bất kỳ) */
function table(headers, rows, ratio) {
  const n = headers.length;
  const r = ratio || Array(n).fill(1);
  const sum = r.reduce((a, b) => a + b, 0);
  const widths = r.map((x) => Math.floor((CONTENT_W * x) / sum));
  widths[n - 1] += CONTENT_W - widths.reduce((a, b) => a + b, 0);
  const border = { style: BorderStyle.SINGLE, size: 4, color: "999999" };
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: widths,
    borders: { top: border, bottom: border, left: border, right: border, insideHorizontal: border, insideVertical: border },
    rows: [
      new TableRow({ tableHeader: true, children: headers.map((h, i) => cell(h, { header: true, width: widths[i], fill: "D9E2F3" })) }),
      ...rows.map((row, ri) => new TableRow({ children: row.map((c, i) => cell(c, { width: widths[i], fill: ri % 2 ? "F7F9FC" : undefined })) })),
    ],
  });
}

function caption(text) {
  return new Paragraph({ children: runs(text, { italics: true, size: 22 }), alignment: AlignmentType.CENTER, spacing: { before: 60, after: 200 } });
}
function spacer() { return new Paragraph({ text: "", spacing: { after: 120 } }); }
function pageBreak() { return new Paragraph({ children: [new PageBreak()] }); }

function image(buffer, type, widthPx, heightPx) {
  return new Paragraph({ children: [new ImageRun({ data: buffer, type, transformation: { width: widthPx, height: heightPx } })], alignment: AlignmentType.CENTER, spacing: { before: 120, after: 60 } });
}

const numbering = {
  config: [
    { reference: "bullets", levels: [
      { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
      { level: 1, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1440, hanging: 360 } } } },
    ] },
    { reference: "numbers", levels: [
      { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
      { level: 1, format: LevelFormat.LOWER_LETTER, text: "%2)", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1440, hanging: 360 } } } },
    ] },
  ],
};

const styles = {
  default: { document: { run: { font: FONT, size: 26 } } },
  paragraphStyles: [
    { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 32, bold: true, font: FONT, color: "1F3864" }, paragraph: { spacing: { before: 360, after: 160 }, outlineLevel: 0 } },
    { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 28, bold: true, font: FONT, color: "2E5496" }, paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1 } },
    { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true, run: { size: 26, bold: true, italics: true, font: FONT, color: "2E5496" }, paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2 } },
  ],
};

module.exports = { docx, h1, h2, h3, p, note, bullet, numbered, code, table, caption, spacer, pageBreak, image, numbering, styles, FONT, PAGE_W, MARGIN, CONTENT_W, TableOfContents, runs };
