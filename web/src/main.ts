import './styles/base.css';
import './styles/theme-modern-fonts.css';
import './styles/theme-modern.css';
import './styles/theme-win95.css';
import './styles/theme-amiga.css';
import { App } from './ui/app';

const root = document.getElementById('app')!;
new App(root).boot().catch((e) => {
  root.textContent = `failed to start: ${(e as Error).message}`;
});
