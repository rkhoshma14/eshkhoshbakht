/**
 * scene3d.js — visual-only 3D tunnel background
 * Does not touch app state, API, or UI logic.
 * Respects prefers-reduced-motion.
 */
(function () {
  const reduced =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const scene = document.getElementById("scene3d");
  const inner = document.getElementById("scene3dInner");
  if (!scene || !inner) return;

  if (reduced) {
    scene.classList.add("reduced");
    return;
  }

  let targetX = 0;
  let targetY = 0;
  let curX = 0;
  let curY = 0;
  let scrollY = 0;
  let raf = 0;

  function onMove(e) {
    const x = e.clientX ?? (e.touches && e.touches[0]?.clientX) ?? 0;
    const y = e.clientY ?? (e.touches && e.touches[0]?.clientY) ?? 0;
    const cx = window.innerWidth / 2;
    const cy = window.innerHeight / 2;
    // subtle tilt range ±6deg
    targetX = ((y - cy) / cy) * -6;
    targetY = ((x - cx) / cx) * 6;
  }

  function onScroll() {
    scrollY = window.scrollY || document.documentElement.scrollTop || 0;
  }

  function tick() {
    curX += (targetX - curX) * 0.06;
    curY += (targetY - curY) * 0.06;
    const parallax = scrollY * 0.04;
    inner.style.transform =
      "translate(-50%, calc(-50% + " +
      parallax +
      "px)) rotateX(" +
      curX +
      "deg) rotateY(" +
      curY +
      "deg)";
    raf = requestAnimationFrame(tick);
  }

  window.addEventListener("mousemove", onMove, { passive: true });
  window.addEventListener("touchmove", onMove, { passive: true });
  window.addEventListener("scroll", onScroll, { passive: true });
  // also observe main content scroll if any nested scroller appears later
  onScroll();
  raf = requestAnimationFrame(tick);

  // pause when tab hidden
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      cancelAnimationFrame(raf);
    } else {
      raf = requestAnimationFrame(tick);
    }
  });
})();
