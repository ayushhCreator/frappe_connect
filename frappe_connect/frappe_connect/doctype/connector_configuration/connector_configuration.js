frappe.ui.form.on("Connector Configuration", {
	onload(frm) {
		frappe.call({
			method: "frappe_connect.event_engine.registry.get_connector_types",
			callback: (r) => {
				frm.set_df_property("connector_type", "options", r.message || []);
				frm.refresh_field("connector_type");
			},
		});
	},
});
