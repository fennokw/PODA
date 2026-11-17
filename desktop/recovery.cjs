'use strict';
// One recovery operation owns the backend at a time. Never replay user actions.
function createRecovery({ probe, start, stop, delay, isClosing, onReady }) {
  let pending = null;
  let starts = [];
  return function ensure({ force = false } = {}) {
    if (pending) return pending;
    pending = (async () => {
      if (isClosing()) return false;
      if (!force) {
        for (let i = 0; i < 3; i++) {
          if (await probe()) return false;
          if (i < 2) await delay(500);
        }
      }
      // Stop only PODA's own supervised child. No PID guessing or lock deletion.
      starts = starts.filter(t => Date.now() - t < 300000);
      if (starts.length >= 3) throw new Error('The local server repeatedly stopped. Check the server log and reopen PODA.');
      starts.push(Date.now());
      await stop();
      if (isClosing()) return false;
      const origin = await start();
      if (!isClosing()) await onReady(origin);
      return true;
    })().finally(() => { pending = null; });
    return pending;
  };
}
module.exports = { createRecovery };
