// Sidebar and Mobile Navigation - DeliveryNotes App

document.addEventListener('DOMContentLoaded', function() {
  // Mobile menu toggle
  const mobileMenuButton = document.querySelector('.mobile-menu-button');
  const sidebar = document.querySelector('.sidebar');
  const mainContent = document.querySelector('.main-content');

  if (mobileMenuButton && sidebar) {
    mobileMenuButton.addEventListener('click', function() {
      sidebar.classList.toggle('open');
    });

    // Close sidebar when clicking outside
    mainContent?.addEventListener('click', function() {
      if (sidebar.classList.contains('open')) {
        sidebar.classList.remove('open');
      }
    });
  }

  // Active navigation highlighting
  const currentPath = window.location.pathname;
  const navLinks = document.querySelectorAll('.sidebar-nav-link, .bottom-nav-link');

  navLinks.forEach(link => {
    const linkPath = new URL(link.href).pathname;

    // Exact match or starts with (for sub-pages)
    if (linkPath === currentPath ||
        (linkPath !== '/' && currentPath.startsWith(linkPath))) {
      link.classList.add('active');
    }
  });

  // Logout button functionality
  const logoutButton = document.querySelector('.logout-button');
  if (logoutButton) {
    logoutButton.addEventListener('click', function(e) {
      e.preventDefault();

      // Submit the logout form
      const logoutForm = document.getElementById('logout-form');
      if (logoutForm) {
        logoutForm.submit();
      }
    });
  }
});
