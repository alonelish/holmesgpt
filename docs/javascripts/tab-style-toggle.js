// Tab styling toggle for testing different visual boundaries
(function() {
  const styles = {
    'none': '',
    'border-bottom': `
      .tabbed-set .tabbed-content {
        border-bottom: 2px solid #e0e0e0;
        margin-bottom: 1.5em;
        padding-bottom: 1em;
      }
      [data-md-color-scheme="slate"] .tabbed-set .tabbed-content {
        border-bottom-color: #404040;
      }
    `,
    'full-border': `
      .tabbed-set {
        border: 1px solid #e0e0e0;
        border-radius: 4px;
        margin-bottom: 1.5em;
        padding: 0 1em 1em 1em;
      }
      .tabbed-set .tabbed-content {
        padding-top: 0.5em;
      }
      [data-md-color-scheme="slate"] .tabbed-set {
        border-color: #404040;
      }
    `
  };

  let currentStyle = localStorage.getItem('tabStyle') || 'none';
  let styleEl = null;

  function applyStyle(style) {
    if (!styleEl) {
      styleEl = document.createElement('style');
      styleEl.id = 'tab-style-override';
      document.head.appendChild(styleEl);
    }
    styleEl.textContent = styles[style] || '';
    currentStyle = style;
    localStorage.setItem('tabStyle', style);
    updateToggleLabel();
  }

  function updateToggleLabel() {
    const label = document.getElementById('tab-style-label');
    if (label) {
      label.textContent = `Tab style: ${currentStyle}`;
    }
  }

  function createToggle() {
    const toggle = document.createElement('div');
    toggle.id = 'tab-style-toggle';
    toggle.innerHTML = `
      <style>
        #tab-style-toggle {
          position: fixed;
          bottom: 20px;
          right: 20px;
          background: #333;
          color: white;
          padding: 10px 15px;
          border-radius: 8px;
          font-size: 12px;
          z-index: 9999;
          box-shadow: 0 2px 10px rgba(0,0,0,0.3);
          font-family: system-ui, sans-serif;
        }
        #tab-style-toggle select {
          margin-left: 8px;
          padding: 4px 8px;
          border-radius: 4px;
          border: none;
          background: #555;
          color: white;
          cursor: pointer;
        }
        #tab-style-toggle select:hover {
          background: #666;
        }
      </style>
      <span id="tab-style-label">Tab style:</span>
      <select id="tab-style-select">
        <option value="none">None</option>
        <option value="border-bottom">Border Bottom</option>
        <option value="full-border">Full Border</option>
      </select>
    `;
    document.body.appendChild(toggle);

    const select = document.getElementById('tab-style-select');
    select.value = currentStyle;
    select.addEventListener('change', (e) => applyStyle(e.target.value));
  }

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      createToggle();
      applyStyle(currentStyle);
    });
  } else {
    createToggle();
    applyStyle(currentStyle);
  }
})();
