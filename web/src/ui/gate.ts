/** Autoplay gate: the AudioContext is created in this gesture. Mini-gate when the context gets suspended. */
import { h } from './dom';

export function showGate(root: HTMLElement, title: string, text: string, button: string): Promise<void> {
  return new Promise((resolve) => {
    const btn = h('button', { class: 'btn primary', type: 'button', autofocus: true }, button);
    const id = `gate-title-${Math.random().toString(36).slice(2, 8)}`;
    const box = h('div', { class: 'gate-box', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': id }, h('h1', { id }, title), h('p', null, text), btn);
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
