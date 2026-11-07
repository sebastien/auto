"""Module: wiring — Composition root for reference resolvers.

The runtime owns the scheme registry; features register their resolvers here so
the runtime never imports a feature module. This is the only place that knows
both sides of the reference boundary.
"""

from __future__ import annotations

from auto.runtime.model import Context

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


def installResolvers(context: Context) -> None:
	"""Install the built-in reference schemes once per context."""
	if context.resolvers:
		return
	from auto.features.elaboration.catalogue import catalogueContent
	from auto.features.plan.references import planContent
	context.resolvers.update({
		"auto:catalogue/": catalogueContent,
		"auto:": planContent,
		"plan:": planContent,
		"task:": planContent,
		"run:": planContent,
		"revision:": planContent,
		"gap:": planContent,
		"decision:": planContent,
		"memo:": lambda ctx, target: ctx.runtime.knowledgeFor().readReference(target.partition(":")[2]),
	})

# EOF
