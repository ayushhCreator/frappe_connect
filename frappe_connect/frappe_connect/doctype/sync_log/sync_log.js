frappe.ui.form.on("Sync Log", {
	refresh(frm) {
		if (frm.doc.status === "Success") return;

		frm.add_custom_button(__("Retry Job"), () => {
			frappe.call({
				method: "frappe_connect.event_engine.dispatcher.retry_sync",
				args: { sync_log_name: frm.doc.name },
				freeze: true,
				callback: () => {
					frappe.show_alert({ message: __("Retry queued"), indicator: "green" });
				},
			});
		});
	},
});
