import frappe


def execute():
    """Backfill Frappe System Roles from existing Branch User rows.

    Until this patch, Branch User.role (Russian: Менеджер/Продавец/...) and the
    Frappe System Role (Branch Manager/Salesperson/...) were maintained in
    parallel with no enforcement. We now treat Branch User.role as the source
    of truth and derive the corresponding system role automatically.

    For each distinct user that appears in at least one active Branch User row,
    call sync_user_system_roles_from_branch_users to add any missing system role.
    """
    from nasiya365.permissions import sync_user_system_roles_from_branch_users

    users = frappe.db.sql_list(
        """SELECT DISTINCT `user`
           FROM `tabBranch User`
           WHERE `is_active` = 1 AND `user` IS NOT NULL AND `user` != ''"""
    )
    for user in users or []:
        try:
            sync_user_system_roles_from_branch_users(user)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"sync_branch_user_system_roles: failed for {user}",
            )
    frappe.db.commit()
