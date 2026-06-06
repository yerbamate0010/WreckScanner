let adminAuthenticated = false;
let pendingAdminLoginResolve = null;

function setAdminAuthenticated(value) {
    const previous = adminAuthenticated;
    adminAuthenticated = Boolean(value);
    document.querySelectorAll('.admin-only').forEach(el => { el.hidden = !adminAuthenticated; });
    if (typeof updateSettingsAccess === 'function') updateSettingsAccess();
    if (typeof updatePublicLayerAccess === 'function') updatePublicLayerAccess();
    const btn = document.getElementById('admin-login');
    if (btn) {
        btn.classList.toggle('is-admin', adminAuthenticated);
        btn.title = adminAuthenticated ? t('icon.adminLogout') : t('icon.adminLogin');
    }
    const panelBtn = document.getElementById('open-admin-panel');
    if (panelBtn) {
        panelBtn.classList.toggle('is-admin', adminAuthenticated);
    }
    if (previous !== adminAuthenticated) {
        if (typeof loadSavedWrecks === 'function') loadSavedWrecks();
        if (typeof loadFieldPhotos === 'function') {
            loadFieldPhotos();
        }
    }
}

async function refreshAdminStatus() {
    try {
        const resp = await fetch(ADMIN_STATUS_URL, { cache: 'no-store' });
        const data = await resp.json();
        setAdminAuthenticated(resp.ok && data.authenticated === true);
    } catch (_) {
        setAdminAuthenticated(false);
    }
}

async function adminLogin() {
    if (adminAuthenticated) return true;
    return new Promise(resolve => {
        pendingAdminLoginResolve = resolve;
        const form = document.getElementById('admin-login-form');
        const status = document.getElementById('admin-login-status');
        const submit = document.getElementById('admin-login-submit');
        form?.reset();
        if (status) status.textContent = '';
        if (submit) {
            submit.disabled = false;
            submit.querySelector('span').textContent = t('modal.admin.submit');
        }
        openModal('modal-admin-login');
        requestAnimationFrame(() => document.getElementById('admin-password-input')?.focus());
    });
}

function closeAdminLoginModal(success = false, target = null) {
    if (target instanceof Element && !target.classList.contains('modal-backdrop')) return;
    const modal = document.getElementById('modal-admin-login');
    if (modal) modal.hidden = true;
    if (!success && pendingAdminLoginResolve) {
        pendingAdminLoginResolve(false);
        pendingAdminLoginResolve = null;
    }
}

async function submitAdminLogin(event) {
    event.preventDefault();
    const passwordInput = document.getElementById('admin-password-input');
    const status = document.getElementById('admin-login-status');
    const submit = document.getElementById('admin-login-submit');
    const password = passwordInput?.value || '';
    if (!password) return;
    if (submit) {
        submit.disabled = true;
        submit.querySelector('span').textContent = t('modal.admin.loggingIn');
    }
    if (status) status.textContent = '';
    let resp;
    let data = {};
    try {
        resp = await fetch(ADMIN_LOGIN_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password }),
        });
        data = await resp.json().catch(() => ({}));
    } catch (err) {
        if (status) status.textContent = err.message || t('admin.loginError');
        if (submit) {
            submit.disabled = false;
            submit.querySelector('span').textContent = t('modal.admin.submit');
        }
        return;
    }
    if (!resp.ok || data.authenticated !== true) {
        if (status) status.textContent = data.error || t('admin.loginError');
        setAdminAuthenticated(false);
        if (submit) {
            submit.disabled = false;
            submit.querySelector('span').textContent = t('modal.admin.submit');
        }
        passwordInput?.focus();
        return;
    }
    setAdminAuthenticated(true);
    if (pendingAdminLoginResolve) {
        pendingAdminLoginResolve(true);
        pendingAdminLoginResolve = null;
    }
    closeAdminLoginModal(true);
}

async function toggleAdminLogin() {
    if (adminAuthenticated) {
        await fetch(ADMIN_LOGOUT_URL, { method: 'POST' }).catch(() => {});
        setAdminAuthenticated(false);
        closeModal();
        return;
    }
    await adminLogin();
}

async function ensureAdmin() {
    if (adminAuthenticated) return true;
    await refreshAdminStatus();
    if (adminAuthenticated) return true;
    return adminLogin();
}

async function openSettingsModal() {
    await refreshAdminStatus();
    if (typeof updateSettingsAccess === 'function') updateSettingsAccess();
    openModal('modal-settings');
}

async function openAdminPanel() {
    if (!(await ensureAdmin())) return;
    if (typeof updatePublicLayerAccess === 'function') updatePublicLayerAccess();
    if (typeof loadPhotoRetentionStatus === 'function') loadPhotoRetentionStatus();
    openModal('modal-admin-panel');
}
