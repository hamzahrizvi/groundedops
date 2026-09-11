<?php
/**
 * Plugin Name:       GroundedOps Support Widget
 * Plugin URI:        https://github.com/hamzahrizvi/groundedops
 * Description:       Adds the GroundedOps support widget to the site. Branding and the contact forms are configured in the GroundedOps console, not here. Signed-in users get AI answers from the product documentation; guests get curated FAQ answers unless guest AI is enabled in the console.
 * Version:           15.3
 * Requires at least: 5.6
 * Requires PHP:      7.4
 * Author:            Innovative Technology
 * License:           MIT
 */

/**
 * INSTALL
 *   1. Zip the folder containing this file, so the archive looks like:
 *        groundedops-widget/groundedops-widget.php
 *   2. WP Admin > Plugins > Add New > Upload Plugin > choose the zip.
 *   3. Activate.
 *   4. Add the two settings to wp-config.php (see SETTINGS below).
 *
 * SETTINGS - put these in wp-config.php, ABOVE the line that says
 * "That's all, stop editing! Happy publishing.":
 *
 *     define('GROUNDEDOPS_API',    'https://support.yourcompany.com');
 *     define('GROUNDEDOPS_SECRET', 'the-32-char-secret-from-openssl-rand');
 *
 * The secret lives in wp-config.php rather than in this file or the
 * database because wp-config is outside the web root on most hosts, is not
 * served by the theme, and does not end up in a plugin export or a
 * database dump handed to a developer.
 *
 * The SAME secret must be set as WIDGET_TOKEN_SECRET in the GroundedOps
 * .env. Use a DIFFERENT secret on staging than production.
 *
 * STAGING
 *   Point GROUNDEDOPS_API at the staging backend and give staging its own
 *   GROUNDEDOPS_SECRET. Two things to check on the GroundedOps side:
 *
 *     WIDGET_ALLOWED_ORIGINS must list this site's origin, or the browser
 *     blocks every call with a CORS error.
 *
 *     WIDGET_TOKEN_SECRET must equal GROUNDEDOPS_SECRET here, or every
 *     signed-in visitor is silently treated as a guest — which looks like
 *     "the AI does not work" rather than an auth mismatch.
 *
 *   To prove the backend half independently of WordPress, mint a token on
 *   the server with `python mint_token.py` and paste it as data-token on a
 *   plain HTML page. If that works and this does not, the fault is here or
 *   in the secret, not in the backend.
 *
 * WHAT IS CONFIGURED WHERE
 *   Here (wp-config.php):  backend URL, shared secret, token TTL.
 *   GroundedOps console:   the assistant's name, colour, icon, welcome
 *                          text, opening buttons, and the sales/support
 *                          forms including their destination addresses.
 */

if (!defined('ABSPATH')) { exit; }

// --- Configuration ---------------------------------------------------
// Prefer wp-config.php constants so the secret is not in the theme, which
// is world-readable if file permissions are ever wrong and ends up in
// backups and version control.
if (!defined('GROUNDEDOPS_API')) {
    define('GROUNDEDOPS_API', 'https://support.yourcompany.com');
}
if (!defined('GROUNDEDOPS_SECRET')) {
    define('GROUNDEDOPS_SECRET', ''); // set in wp-config.php
}

// How long a token is valid. Short enough that a leaked token expires on
// its own; long enough that a normal session never sees it expire.
if (!defined('GROUNDEDOPS_TOKEN_TTL')) {
    define('GROUNDEDOPS_TOKEN_TTL', 8 * HOUR_IN_SECONDS);
}

/**
 * Where the widget's "Sign in for a full answer" button sends a guest.
 *
 * This site authenticates through Azure AD B2C over SAML, so the actual
 * login URL contains a freshly signed, single-use SAMLRequest. It CANNOT be
 * hardcoded - a captured one fails signature/replay validation for the next
 * person. Point at the page that INITIATES the flow instead and let the SP
 * plugin mint a new request each time.
 *
 * /my-account/ is the natural choice here: visiting it while signed out
 * starts the SSO flow, and the visitor lands somewhere useful afterwards.
 */
if (!defined('GROUNDEDOPS_LOGIN_URL')) {
    define('GROUNDEDOPS_LOGIN_URL', home_url('/my-account/'));
}

/**
 * Base64url without padding - matches Python's
 * base64.urlsafe_b64encode(...).rstrip(b'=')
 */
function groundedops_b64url($data) {
    return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
}

/**
 * Mint a token for the current user.
 *
 * Format: base64url(json_payload) . "." . base64url(hmac_sha256)
 * Payload: {uid, tier, exp}
 *
 * The user id is the WordPress user ID, not the email or login name: the
 * backend only needs a stable identifier to count credits against, and
 * sending an email address would put personal data in a bearer token that
 * lives in browser memory for no benefit.
 */
function groundedops_make_token() {
    if (!is_user_logged_in() || GROUNDEDOPS_SECRET === '') {
        return '';
    }
    $user = wp_get_current_user();

    // Staff tier for roles that should get a larger allowance. Adjust to
    // match your site's roles.
    $tier = 'member';
    $staff_roles = array('administrator', 'editor', 'support_agent');
    if (array_intersect($staff_roles, (array) $user->roles)) {
        $tier = 'staff';
    }

    $payload = wp_json_encode(array(
        'uid'  => (string) $user->ID,
        'tier' => $tier,
        'exp'  => time() + GROUNDEDOPS_TOKEN_TTL,
    ));

    $raw = groundedops_b64url($payload);
    $sig = hash_hmac('sha256', $raw, GROUNDEDOPS_SECRET, true);

    return $raw . '.' . groundedops_b64url($sig);
}

/**
 * Emit the widget script tag in the footer.
 *
 * Footer rather than header: the widget builds DOM on load, and a header
 * script would either block rendering or need a readiness guard. Nothing
 * about it needs to run before the page paints.
 */
function groundedops_embed_widget() {
    // Optional: skip on admin screens and the login page, where a support
    // widget is noise.
    if (is_admin()) { return; }

    $api   = esc_url(GROUNDEDOPS_API);
    $token = groundedops_make_token();

    // Only what WordPress is the authority on.
    //
    // data-agent-name and data-accent were set here as 'Support' and
    // '#E4002B'. They have been REMOVED on purpose: the widget now reads its
    // branding from GET /widget/config, i.e. from the console's Widget
    // design page, and a data-* attribute that is explicitly present
    // OVERRIDES the server. Leaving them here meant editing the branding in
    // the console had no effect on the live WordPress site — the exact bug
    // that page was fixed to stop having.
    //
    // What stays is what only WordPress can know: where the backend is, who
    // this visitor is, and where to send them to sign in. To brand the
    // widget, use the console. To override it for one site anyway, add the
    // attribute back here deliberately.
    $attrs = array(
        'src'              => $api . '/widget/groundedops-widget.js',
        'data-api'         => $api,
        // Return the visitor to the page they were reading, if the SP
        // plugin honours redirect_to (WooCommerce and most SAML plugins do).
        'data-sign-in-url' => esc_url(add_query_arg(
            'redirect_to', urlencode(get_permalink() ?: home_url('/')),
            GROUNDEDOPS_LOGIN_URL
        )),
    );
    if ($token !== '') {
        $attrs['data-token'] = $token;
    }

    $html = '<script';
    foreach ($attrs as $k => $v) {
        $html .= ' ' . $k . '="' . esc_attr($v) . '"';
    }
    $html .= ' defer></script>';

    echo $html; // phpcs:ignore WordPress.Security.EscapeOutput
}
add_action('wp_footer', 'groundedops_embed_widget', 100);

/**
 * OPTIONAL: token refresh endpoint.
 *
 * A page open for longer than the TTL would otherwise see the user
 * silently demoted to FAQ-only. This lets the widget fetch a fresh token
 * without a page reload. Uses WordPress's own auth cookie, so it only ever
 * issues a token for whoever is actually signed in.
 */
add_action('rest_api_init', function () {
    register_rest_route('groundedops/v1', '/token', array(
        'methods'             => 'GET',
        'permission_callback' => function () { return is_user_logged_in(); },
        'callback'            => function () {
            return array(
                'token'      => groundedops_make_token(),
                'expires_in' => GROUNDEDOPS_TOKEN_TTL,
            );
        },
    ));
});


/**
 * Warn in the admin if the plugin is active but not configured. Without
 * this the failure is silent: every visitor is treated as a guest, the
 * widget still works, and nobody can tell that AI answers are switched off
 * because the secret is missing.
 */
add_action('admin_notices', function () {
    if (!current_user_can('manage_options')) { return; }
    $problems = array();
    if (!defined('GROUNDEDOPS_SECRET') || GROUNDEDOPS_SECRET === '') {
        $problems[] = 'GROUNDEDOPS_SECRET is not set, so signed-in users will not get AI answers.';
    }
    if (!defined('GROUNDEDOPS_API') || strpos(GROUNDEDOPS_API, 'yourcompany.com') !== false) {
        $problems[] = 'GROUNDEDOPS_API still points at the example URL.';
    }
    if (!is_user_logged_in() && !defined('GROUNDEDOPS_LOGIN_URL_CHECKED')) {
        // Not an error, just a nudge: with SSO the login URL is easy to get
        // wrong and the failure is a dead button rather than an exception.
        $problems[] = 'Verify GROUNDEDOPS_LOGIN_URL starts the SSO flow correctly (currently ' . esc_html(GROUNDEDOPS_LOGIN_URL) . ').';
    }
    if (defined('GROUNDEDOPS_API') && strpos(GROUNDEDOPS_API, 'https://') !== 0
        && strpos(GROUNDEDOPS_API, 'http://localhost') !== 0) {
        $problems[] = 'GROUNDEDOPS_API is not HTTPS - tokens would be sent in clear text.';
    }
    if (!$problems) { return; }
    echo '<div class="notice notice-warning"><p><strong>GroundedOps Support Widget</strong></p><ul style="list-style:disc;margin-left:20px">';
    foreach ($problems as $p) {
        echo '<li>' . esc_html($p) . '</li>';
    }
    echo '</ul><p>Add these to <code>wp-config.php</code>.</p></div>';
});
