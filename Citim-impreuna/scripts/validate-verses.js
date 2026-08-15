const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadVerseArray(fileName, globalName) {
  const source = fs.readFileSync(fileName, "utf8");
  const context = {};
  vm.createContext(context);
  vm.runInContext(`${source}\nglobalThis.__verses = ${globalName};`, context, { filename: fileName });
  return context.__verses;
}

function validate(book, fileName, globalName) {
  const verses = loadVerseArray(path.resolve(__dirname, "..", fileName), globalName);
  const errors = [];
  const refs = new Set();

  for (const verse of verses) {
    if (!verse.ref.startsWith(`${book} `)) errors.push(`${verse.ref}: wrong book reference`);
    if (refs.has(verse.ref)) errors.push(`${verse.ref}: duplicate reference`);
    refs.add(verse.ref);

    const blanks = (verse.text.match(/\{0\}/g) || []).length;
    if (blanks !== 1) errors.push(`${verse.ref}: expected one {0}, found ${blanks}`);
    if (!Array.isArray(verse.blanks) || verse.blanks.length !== 1) {
      errors.push(`${verse.ref}: expected one blank definition`);
      continue;
    }

    const blank = verse.blanks[0];
    if (!Array.isArray(blank.options) || new Set(blank.options).size !== 4) {
      errors.push(`${verse.ref}: expected four unique options`);
    }
    if (!blank.options.includes(blank.answer)) errors.push(`${verse.ref}: answer is missing from options`);

    const escaped = blank.answer.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const answerInText = new RegExp(`(^|[^\\p{L}\\p{M}])${escaped}(?=$|[^\\p{L}\\p{M}])`, "iu");
    if (answerInText.test(verse.text.replace("{0}", ""))) {
      errors.push(`${verse.ref}: answer repeats outside the blank`);
    }
  }

  console.log(`${book}: ${verses.length} verses checked`);
  if (errors.length > 0) {
    console.error(errors.join("\n"));
    process.exitCode = 1;
  } else {
    console.log("No validation errors.");
  }
}

module.exports = { validate };
