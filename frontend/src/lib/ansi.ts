// Strip ANSI escape sequences (CSI, OSC, single-char escapes) from terminal
// output so tmux pane content renders as plain text.
// Regexes are built from the ESC code at runtime; a literal control character
// in a regex would trip eslint no-control-regex.

const ESC = String.fromCharCode(27)
const BEL = String.fromCharCode(7)

const CSI = new RegExp(`${ESC}\\[[0-9;?]*[ -/]*[@-~]`, 'g')
const OSC = new RegExp(`${ESC}\\][^${BEL}${ESC}]*(?:${BEL}|${ESC}\\\\)?`, 'g')
const SINGLE = new RegExp(`${ESC}[@-Z\\\\-_]`, 'g')

export function stripAnsi(text: string): string {
  return text.replace(OSC, '').replace(CSI, '').replace(SINGLE, '')
}
