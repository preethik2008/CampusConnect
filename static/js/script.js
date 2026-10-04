/* CampusConnect JavaScript */

function toggleMenu() {
    const nav = document.getElementById("navLinks");
    if (nav) nav.classList.toggle("active");
}

function confirmDelete() {
    return confirm("Are you sure you want to delete this event? This also removes its registrations.");
}

document.addEventListener("DOMContentLoaded", function () {
    // Progress bars: width is set here so no template code lives inside CSS
    document.querySelectorAll(".progress-fill").forEach(function (bar) {
        const value = parseFloat(bar.dataset.progress || 0);
        bar.style.width = Math.max(0, Math.min(100, value)) + "%";
    });

    // Auto-hide flash messages
    document.querySelectorAll(".flash-message").forEach(function (msg) {
        setTimeout(function () {
            msg.style.opacity = "0";
            msg.style.transform = "translateY(-5px)";
            setTimeout(function () { msg.remove(); }, 300);
        }, 5000);
    });
});
