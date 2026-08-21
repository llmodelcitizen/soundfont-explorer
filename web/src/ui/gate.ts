/** Modal 'audio suspended — tap to resume' dialog; `inGesture` runs synchronously in the tap so it may resume the AudioContext. */
import { h } from './dom';

export function showGate(root: HTMLElement, title: string, text: string, button: string, inGesture?: () => void): Promise<void> {
  return new Promise((resolve) => {
    const btn = h('button', { class: 'btn primary', type: 'button', autofocus: true }, button);
    const id = `gate-title-${Math.random().toString(36).slice(2, 8)}`;
    const box = h('div', { class: 'gate-box', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': id }, h('h1', { id }, title), h('p', null, text), btn);
    const overlay = h('div', { class: 'gate' }, box);
    const go = () => {
      try {
        inGesture?.(); // synchronous: an AudioContext resume must happen in the gesture's call stack
      } catch {
        /* reported by the caller through its own state */
      }
      overlay.remove();
      resolve();
    };
    btn.addEventListener('click', go);
    overlay.addEventListener('keydown', (e) => {
      e.stopPropagation(); // the player's key map must not see keys meant for the gate
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        go();
      }
    });
    root.appendChild(overlay);
    btn.focus();
  });
}
