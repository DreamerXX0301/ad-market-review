export function chartPalette() {
  const light = document.documentElement.dataset.theme === "light";
  return {
    text: light ? "#768298" : "#7d8ba0",
    axisText: light ? "#586980" : "#9aacc3",
    line: light ? "#e6eaf0" : "#202b3a",
    bg: light ? "#ffffff" : "#111924",
    tooltip: light ? "#ffffff" : "#192332",
    tooltipText: light ? "#34435a" : "#dce6f5",
    provisional: light ? "#875b12" : "#efba55",
    reference: light ? "#7754b4" : "#b99afa",
    up: light ? "#d85151" : "#ed6865",
    down: light ? "#119573" : "#39bd9b",
    bollUpper: light ? "#ce4147" : "#f07178",
    bollMiddle: light ? "#00868f" : "#49c7db",
    bollLower: light ? "#228447" : "#57cc83",
    waveCorrection: light ? "#7754b4" : "#b292f0",
    waveTurning: light ? "#758496" : "#a0aec1",
    trendFocus: light ? "#792bb0" : "#e6b4ff",
    trendFocusText: light ? "#fff" : "#241333",
  };
}
