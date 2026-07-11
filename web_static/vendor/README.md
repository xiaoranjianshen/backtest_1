# Report JavaScript Dependencies

These pinned browser libraries are embedded into generated HTML reports so the
charts and PDF export work on computers without internet access.

- `html2canvas.min.js`: html2canvas 1.4.1, MIT License,
  https://github.com/niklasvh/html2canvas
- `jspdf.umd.min.js`: jsPDF 2.5.1, MIT License,
  https://github.com/parallax/jsPDF

Plotly JavaScript is loaded from the installed Python `plotly` package at report
generation time, so its browser version stays aligned with the Python runtime.
