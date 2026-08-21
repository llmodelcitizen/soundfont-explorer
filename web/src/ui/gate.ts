/** Autoplay gate: the AudioContext is created in this gesture. Mini-gate when the context gets suspended. */
import { h } from './dom';

export function showGate(root: HTMLElement, title: string, text: string, button: string): Promise<void> {
  return new Promise((resolve) => {
    const btn = h('button', { class: 'btn primary', type: 'button', autofocus: true }, button);
    const box = h('div', { class: 'gate-box', role: 'dialog', 'aria-modal': 'true' }, h('h1', null, title), h('p', null, text), btn);
    const overlay = h('div', { class: 'gate' }, box);
    const go = () => {
      overlay.remove();
      resolve();
    };
    btn.addEventListener('click', go);
    overlay.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        go();
      }
    });
    root.appendChild(overlay);
    btn.focus();
  });
}
