// Marks the page as having scripts before anything is drawn, so content
// waiting for its reveal animation is hidden only when that animation will
// actually run. Without scripts, every section simply shows.
document.documentElement.classList.add("js");
