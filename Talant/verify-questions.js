'use strict';
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('questions.js', 'utf8')
  .replace(/^\s*(?:const|let|var)\s+QUESTIONS\s*=/m, 'QUESTIONS =');
const context = {};
vm.createContext(context);
vm.runInContext(source, context, { timeout: 1000 });
const questions = context.QUESTIONS;

if (!Array.isArray(questions) || questions.length === 0) throw new Error('QUESTIONS must be a non-empty array.');
for (const [index, question] of questions.entries()) {
  if (!question.card || !question.question || !question.feedback || !Array.isArray(question.answers)) throw new Error(`Question ${index} is missing required fields.`);
  const correct = Array.isArray(question.correct) ? question.correct : [question.correct];
  if (!correct.length || correct.some((answer) => !Number.isInteger(answer) || answer < 0 || answer >= question.answers.length)) {
    throw new Error(`Question ${index} has an invalid correct answer index.`);
  }
}
console.log(`Validated ${questions.length} questions.`);
