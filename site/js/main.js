/* ============================================
   АвтоДиагностика AI — Main JavaScript
   Navigation, scroll effects, reveal animations
   ============================================ */

document.addEventListener('DOMContentLoaded', () => {

    // ── DOM references ───────────────────────
    const nav = document.getElementById('nav');
    const navLinks = document.getElementById('navLinks');
    const mobileToggle = document.getElementById('mobileToggle');
    const allNavLinks = navLinks.querySelectorAll('a[href^="#"]');
    const revealElements = document.querySelectorAll('.reveal');
    const screenshotsScroll = document.getElementById('screenshotsScroll');

    // ── Mobile menu toggle ──────────────────
    mobileToggle.addEventListener('click', () => {
        const isOpen = navLinks.classList.toggle('open');
        mobileToggle.classList.toggle('open', isOpen);
        document.body.style.overflow = isOpen ? 'hidden' : '';
    });

    // Close mobile menu on link click
    allNavLinks.forEach(link => {
        link.addEventListener('click', () => {
            navLinks.classList.remove('open');
            mobileToggle.classList.remove('open');
            document.body.style.overflow = '';
        });
    });

    // Close on outside click
    document.addEventListener('click', (e) => {
        if (navLinks.classList.contains('open') &&
            !nav.contains(e.target)) {
            navLinks.classList.remove('open');
            mobileToggle.classList.remove('open');
            document.body.style.overflow = '';
        }
    });

    // ── Scroll: nav background ──────────────
    let lastScroll = 0;
    function onScroll() {
        const scrollY = window.scrollY;
        nav.classList.toggle('scrolled', scrollY > 20);

        // Active link tracking
        let currentSection = '';
        document.querySelectorAll('section[id], header[id]').forEach(section => {
            const top = section.offsetTop - 120;
            if (scrollY >= top) {
                currentSection = section.getAttribute('id');
            }
        });

        allNavLinks.forEach(link => {
            link.classList.toggle('active', link.getAttribute('href') === '#' + currentSection);
        });

        // Scroll reveal
        revealElements.forEach(el => {
            const rect = el.getBoundingClientRect();
            const windowHeight = window.innerHeight;
            if (rect.top < windowHeight - 80) {
                el.classList.add('visible');
            }
        });

        lastScroll = scrollY;
    }

    // Trigger once on load
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });

    // ── Smooth scroll for anchor links ──────
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', (e) => {
            const href = anchor.getAttribute('href');
            if (href === '#') return;

            const target = document.querySelector(href);
            if (target) {
                e.preventDefault();
                const navHeight = nav.offsetHeight;
                const targetTop = target.getBoundingClientRect().top + window.scrollY - navHeight - 8;
                window.scrollTo({ top: targetTop, behavior: 'smooth' });
            }
        });
    });

    // ── Screenshots horizontal scroll ───────
    if (screenshotsScroll) {
        // Mouse wheel horizontal scroll
        screenshotsScroll.addEventListener('wheel', (e) => {
            if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
                e.preventDefault();
                screenshotsScroll.scrollLeft += e.deltaY;
            }
        }, { passive: false });

        // Touch swipe
        let touchStartX = 0;
        let touchStartY = 0;
        let touchScrollLeft = 0;

        screenshotsScroll.addEventListener('touchstart', (e) => {
            touchStartX = e.touches[0].clientX;
            touchStartY = e.touches[0].clientY;
            touchScrollLeft = screenshotsScroll.scrollLeft;
        }, { passive: true });

        screenshotsScroll.addEventListener('touchmove', (e) => {
            const deltaX = e.touches[0].clientX - touchStartX;
            const deltaY = e.touches[0].clientY - touchStartY;

            if (Math.abs(deltaX) > Math.abs(deltaY)) {
                e.preventDefault();
                screenshotsScroll.scrollLeft = touchScrollLeft - deltaX;
            }
        }, { passive: false });
    }

    // ── Keyboard navigation ─────────────────
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && navLinks.classList.contains('open')) {
            navLinks.classList.remove('open');
            mobileToggle.classList.remove('open');
            document.body.style.overflow = '';
        }
    });

    // ── Resize handler ──────────────────────
    let resizeTimeout;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => {
            if (window.innerWidth > 768 && navLinks.classList.contains('open')) {
                navLinks.classList.remove('open');
                mobileToggle.classList.remove('open');
                document.body.style.overflow = '';
            }
        }, 250);
    });

});
