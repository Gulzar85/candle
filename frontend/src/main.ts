import { createIcons } from "lucide";
import Alpine from "alpinejs";
import "@css/main.css";

const isDev = import.meta.env.DEV;

export const CandleApp = {
  initLucide(): void {
    createIcons();
  },

  initAlpine(): void {
    // Alpine auto-initializes via the `x-data` attribute.
    // This exists as an explicit hook for manual Alpine setup if needed.
  },

  initHtmx(): void {
    // htmx auto-initializes from its own script tag.
    // Extend here if custom htmx config or event listeners are required.
  },

  initAll(): void {
    this.initLucide();
    this.initAlpine();
    this.initHtmx();
  },
};

document.addEventListener("DOMContentLoaded", () => {
  CandleApp.initLucide();

  if (isDev) {
    console.log("Candle initialized");
  }
});
