export const AVATAR_STYLE = `
.agent-av { display: block; flex: none; overflow: visible; }
.agent-av * { transform-box: view-box; }
.agent-av-rig { transform-origin: 12px 20.4px; }
.agent-av-leaf { transform-origin: 12.6px 3.9px; }
.agent-av-eye { transform-box: fill-box; transform-origin: center; }
.agent-av-lens { transform-origin: 18.3px 15.6px; }
.agent-av-ring { stroke: var(--bg); fill: none; }
.agent-av-fx { opacity: 0; transition: opacity var(--dur-base, .2s) ease; }
.agent-av-key { opacity: .4; }
.agent-av-dot { transform-box: fill-box; transform-origin: center; }

.agent-av[data-pose] .agent-av-eye { animation: av-blink 4.2s infinite; }
@keyframes av-blink { 0%,91%,97%,100% { transform: scaleY(1); } 94% { transform: scaleY(.12); } }

.agent-av[data-pose=search] .agent-av-eyes { animation: av-s-gaze 2.6s cubic-bezier(.45,0,.2,1) infinite; }
.agent-av[data-pose=search] .agent-av-rig { animation: av-s-tilt 2.6s cubic-bezier(.45,0,.2,1) infinite; }
.agent-av[data-pose=search] .agent-av-leaf { animation: av-s-leaf 2.6s cubic-bezier(.45,0,.2,1) infinite; }
.agent-av[data-pose=search] .agent-av-fx-search { opacity: 1; }
.agent-av[data-pose=search] .agent-av-lens { animation: av-s-lens 2.6s cubic-bezier(.45,0,.2,1) infinite; }
@keyframes av-s-gaze { 0%,100% { transform: translate(0,0); } 15%,40% { transform: translate(-1.6px,.2px); } 55%,80% { transform: translate(1.6px,.2px); } }
@keyframes av-s-tilt { 0%,100% { transform: rotate(0); } 15%,40% { transform: rotate(-3deg); } 55%,80% { transform: rotate(3deg); } }
@keyframes av-s-leaf { 0%,100% { transform: rotate(0); } 20%,42% { transform: rotate(9deg); } 60%,82% { transform: rotate(-9deg); } }
@keyframes av-s-lens { 0%,100% { transform: translate(0,0) rotate(0); } 17%,40% { transform: translate(-2.2px,-.6px) rotate(-8deg); } 57%,80% { transform: translate(.9px,-.3px) rotate(6deg); } }

.agent-av[data-pose=type] .agent-av-eyes { animation: av-t-gaze 2.4s infinite; }
.agent-av[data-pose=type] .agent-av-rig { animation: av-t-bob .26s ease-in-out infinite alternate; }
.agent-av[data-pose=type] .agent-av-leaf { animation: av-t-leaf .52s ease-in-out infinite alternate; }
.agent-av[data-pose=type] .agent-av-fx-type { opacity: 1; }
.agent-av[data-pose=type] .agent-av-key { animation: av-press .62s infinite; }
.agent-av[data-pose=type] .agent-av-key:nth-child(2) { animation-duration: .74s; animation-delay: -.21s; }
.agent-av[data-pose=type] .agent-av-key:nth-child(3) { animation-duration: .53s; animation-delay: -.37s; }
.agent-av[data-pose=type] .agent-av-key:nth-child(4) { animation-duration: .68s; animation-delay: -.09s; }
@keyframes av-t-gaze { 0% { transform: translate(-.9px,1.3px); } 30% { transform: translate(-.2px,1.3px); } 60% { transform: translate(.5px,1.3px); } 86% { transform: translate(1px,1.3px); } 100% { transform: translate(-.9px,1.3px); } }
@keyframes av-t-bob { from { transform: translateY(0); } to { transform: translateY(.45px); } }
@keyframes av-t-leaf { from { transform: rotate(-4deg); } to { transform: rotate(5deg); } }
@keyframes av-press { 0%,30%,100% { transform: translateY(0); opacity: .4; } 8% { transform: translateY(.5px); opacity: 1; } }

.agent-av[data-pose=think] .agent-av-eye { animation-duration: 5.6s; }
.agent-av[data-pose=think] .agent-av-eyes { animation: av-k-gaze 4.8s cubic-bezier(.45,0,.2,1) infinite; }
.agent-av[data-pose=think] .agent-av-rig { animation: av-k-tilt 4.8s cubic-bezier(.45,0,.2,1) infinite; }
.agent-av[data-pose=think] .agent-av-leaf { animation: av-k-leaf 2.4s ease-in-out infinite alternate; }
.agent-av[data-pose=think] .agent-av-fx-think { opacity: 1; }
.agent-av[data-pose=think] .agent-av-dot { animation: av-bubble 1.8s ease-in-out infinite; }
.agent-av[data-pose=think] .agent-av-dot:nth-child(2) { animation-delay: .25s; }
.agent-av[data-pose=think] .agent-av-dot:nth-child(3) { animation-delay: .5s; }
@keyframes av-k-gaze { 0%,45%,100% { transform: translate(1.1px,-1.5px); } 55%,85% { transform: translate(-.6px,-1.7px); } }
@keyframes av-k-tilt { 0%,45%,100% { transform: rotate(3deg); } 55%,85% { transform: rotate(-1.5deg); } }
@keyframes av-k-leaf { from { transform: rotate(-6deg); } to { transform: rotate(6deg); } }
@keyframes av-bubble { 0%,100% { transform: scale(.55); opacity: .25; } 40% { transform: scale(1); opacity: 1; } }

.avatar-live { position: relative; color: var(--accent); background: none; }
.avatar-live .agent-av { position: absolute; left: -3px; top: -6px; }
.spark .agent-av { display: inline-block; }
.brand-live { position: relative; }
.brand-live .agent-av { position: absolute; left: -1px; top: -4px; }
.brand-live .agent-av-ring { stroke: var(--bg-rail); }

.agent-av[data-pose=idle] .agent-av-rig { animation: av-i-breathe 3.4s ease-in-out infinite alternate; }
.agent-av[data-pose=idle] .agent-av-eyes { animation: av-i-glance 7.5s ease-in-out infinite; }
.agent-av[data-pose=idle] .agent-av-eye { animation: av-i-blink 5.2s infinite; }
.agent-av[data-pose=idle] .agent-av-leaf { animation: av-i-leaf 3.4s ease-in-out infinite alternate; }
@keyframes av-i-breathe { from { transform: scale(1,1); } to { transform: scale(1.015,.97); } }
@keyframes av-i-glance { 0%,58%,100% { transform: translate(0,0); } 63%,72% { transform: translate(-1.1px,.2px); } 77%,88% { transform: translate(1.1px,-.2px); } }
@keyframes av-i-blink { 0%,86%,89%,92%,100% { transform: scaleY(1); } 87.5%,90.5% { transform: scaleY(.12); } }
@keyframes av-i-leaf { from { transform: rotate(-3deg); } to { transform: rotate(4deg); } }

@media (prefers-reduced-motion: reduce) {
  .agent-av * { animation: none !important; }
  .agent-av[data-pose=search] .agent-av-eyes { transform: translate(-1.6px,.2px); }
  .agent-av[data-pose=type] .agent-av-eyes { transform: translate(0,1.3px); }
  .agent-av[data-pose=think] .agent-av-eyes { transform: translate(1.1px,-1.5px); }
}
`;
