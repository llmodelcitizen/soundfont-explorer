import { describe, expect, it } from 'vitest';
import { resumeNotice } from '../../src/ui/transport';

/** what the per-frame UI loop paints for a sequence of [ctx.state, engine.playing] frames */
function frames(seq: [string, boolean][]): string[] {
  let was = false;
  return seq.map(([state, playing]) => {
    const r = resumeNotice(state, playing, was);
    was = r.suspended;
    return r.notice ?? (r.restore ? 'restore' : '-');
  });
}

describe('resumeNotice', () => {
  it('takes the notice down on the frame the context comes back, exactly once', () => {
    // the engine repaints its status only when the status changes, so a context that resumed
    // without an engine event left 'tap to resume' up for the rest of the session
    expect(frames([['running', true], ['suspended', true], ['suspended', true], ['running', true], ['running', true]])).toEqual([
      '-',
      'audio suspended — tap to resume',
      'audio suspended — tap to resume',
      'restore',
      '-',
    ]);
  });

  it('says nothing while the transport is not playing', () => {
    // a suspended context before the first play is normal (autoplay policy), not a fault
    expect(frames([['suspended', false], ['suspended', false]])).toEqual(['-', '-']);
  });

  it('restores once when playback stops while the context is still suspended', () => {
    expect(frames([['suspended', true], ['suspended', false], ['suspended', false]])).toEqual([
      'audio suspended — tap to resume',
      'restore',
      '-',
    ]);
  });

  it('names the state the context is actually in', () => {
    expect(resumeNotice('interrupted', true, false).notice).toBe('audio interrupted — tap to resume');
  });
});
