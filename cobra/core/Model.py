from warnings import warn
from copy import deepcopy, copy

import sympy
from six import iteritems, string_types

from cobra.exceptions import SolveError
from ..solvers import optimize
from .Object import Object
from .Solution import Solution, LazySolution
from .Reaction import Reaction
from .DictList import DictList

# from cameo ...
import six
import time
import types
import optlang
from sympy.core.singleton import S
from pandas import DataFrame
from functools import partial
from cobra.util import AutoVivification

from cobra import exceptions, config

# Note, when a reaction is added to the Model it will no longer keep personal
# instances of its Metabolites, it will reference Model.metabolites to improve
# performance.  When doing this, take care to monitor metabolite coefficients.
# Do the same for Model.reactions[:].genes and Model.genes

class Model(Object):
    """Metabolic Model

    Refers to Metabolite, Reaction, and Gene Objects.
    """

    def __setstate__(self, state):
        """Make sure all cobra.Objects in the model point to the model"""
        self.__dict__.update(state)
        for y in ['reactions', 'genes', 'metabolites']:
            for x in getattr(self, y):
                x._model = self
        if not hasattr(self, "name"):
            self.name = None

    def __init__(self, id_or_model=None, name=None, solver_interface=optlang):
        if isinstance(id_or_model, Model):
            Object.__init__(self, name=name)
            self.__setstate__(id_or_model.__dict__)
            if not hasattr(self, "name"):
                self.name = None
            self._solver = id_or_model.solver
        else:
            Object.__init__(self, id_or_model, name=name)
            self._trimmed = False
            self._trimmed_genes = []
            self._trimmed_reactions = {}
            self.genes = DictList()
            self.reactions = DictList()  # A list of cobra.Reactions
            self.metabolites = DictList()  # A list of cobra.Metabolites
            # genes based on their ids {Gene.id: Gene}
            self.compartments = {}
            # self.solution = Solution(None)
            self.media_compositions = {}

            # from cameo ...

            # if not hasattr(self, '_solver'):  # backwards compatibility with older cobrapy pickles?
            self._solver = solver_interface.Model()
            self._solver.objective = solver_interface.Objective(S.Zero)
            self._populate_solver(self.reactions, self.metabolites)
        self._timestamp_last_optimization = None
        self.solution = LazySolution(self)


    @property
    def solver(self):
        """Attached solver instance.

        Very useful for accessing the optimization problem directly. Furthermore, can be used to define additional
        non-metabolic constraints.

        Examples
        --------
        >>> new_constraint_from_objective = model.solver.interface.Constraint(model.objective.expression, lb=0.99)
        >>> model.solver.add(new_constraint)

        """
        return self._solver

    @solver.setter
    def solver(self, value):
        not_valid_interface = ValueError(
            '%s is not a valid solver interface. Pick from %s, or specify an optlang interface (e.g. optlang.glpk_interface).' % (
                value, list(config.solvers.keys())))
        if isinstance(value, six.string_types):
            try:
                interface = config.solvers[value]
            except KeyError:
                raise not_valid_interface
        elif isinstance(value, types.ModuleType) and hasattr(value, 'Model'):
            interface = value
        else:
            raise not_valid_interface
        for reaction in self.reactions:
            reaction._reset_var_cache()
        self._solver = interface.Model.clone(self._solver)

    @property
    def description(self):
        warn("description deprecated")
        return self.name if self.name is not None else ""

    @description.setter
    def description(self, value):
        self.name = value
        warn("description deprecated")

    def __add__(self, other_model):
        """Adds two models. +

        The issue of reactions being able to exists in multiple Models now
        arises, the same for metabolites and such.  This might be a little
        difficult as a reaction with the same name / id in two models might
        have different coefficients for their metabolites due to pH and whatnot
        making them different reactions.

        """
        new_model = self.copy()
        new_reactions = deepcopy(other_model.reactions)
        new_model.add_reactions(new_reactions)
        new_model.id = self.id + '_' + other_model.id
        return new_model

    def __iadd__(self, other_model):
        """Adds a Model to this model +=

        The issue of reactions being able to exists in multiple Models now
        arises, the same for metabolites and such.  This might be a little
        difficult as a reaction with the same name / id in two models might
        have different coefficients for their metabolites due to pH and whatnot
        making them different reactions.

        """
        new_reactions = deepcopy(other_model.reactions)
        self.add_reactions(new_reactions)
        self.id = self.id + '_' + other_model.id
        return self

    def copy(self):
        """Provides a partial 'deepcopy' of the Model.  All of the Metabolite,
        Gene, and Reaction objects are created anew but in a faster fashion
        than deepcopy
        """
        return deepcopy(self)  # TODO: fix the hack below
        # new = self.__class__()
        # do_not_copy = {"metabolites", "reactions", "genes"}
        # for attr in self.__dict__:
        #     if attr not in do_not_copy:
        #         new.__dict__[attr] = self.__dict__[attr]
        #
        # new.metabolites = DictList()
        # do_not_copy = {"_reaction", "_model"}
        # for metabolite in self.metabolites:
        #     new_met = metabolite.__class__()
        #     for attr, value in iteritems(metabolite.__dict__):
        #         if attr not in do_not_copy:
        #             new_met.__dict__[attr] = copy(
        #                 value) if attr == "formula" else value
        #     new_met._model = new
        #     new.metabolites.append(new_met)
        #
        # new.genes = DictList()
        # for gene in self.genes:
        #     new_gene = gene.__class__(None)
        #     for attr, value in iteritems(gene.__dict__):
        #         if attr not in do_not_copy:
        #             new_gene.__dict__[attr] = copy(
        #                 value) if attr == "formula" else value
        #     new_gene._model = new
        #     new.genes.append(new_gene)
        #
        # new.reactions = DictList()
        # do_not_copy = {"_model", "_metabolites", "_genes"}
        # for reaction in self.reactions:
        #     new_reaction = reaction.__class__()
        #     for attr, value in iteritems(reaction.__dict__):
        #         if attr not in do_not_copy:
        #             new_reaction.__dict__[attr] = copy(value)
        #     new_reaction._model = new
        #     new.reactions.append(new_reaction)
        #     # update awareness
        #     for metabolite, stoic in iteritems(reaction._metabolites):
        #         new_met = new.metabolites.get_by_id(metabolite.id)
        #         new_reaction._metabolites[new_met] = stoic
        #         new_met._reaction.add(new_reaction)
        #     for gene in reaction._genes:
        #         new_gene = new.genes.get_by_id(gene.id)
        #         new_reaction._genes.add(new_gene)
        #         new_gene._reaction.add(new_reaction)
        # return new

    def add_metabolites(self, metabolite_list):
        """Will add a list of metabolites to the the object, if they do not
        exist and then expand the stochiometric matrix

        metabolite_list: A list of :class:`~cobra.core.Metabolite` objects

        """
        if not hasattr(metabolite_list, '__iter__'):
            metabolite_list = [metabolite_list]
        # First check whether the metabolites exist in the model
        metabolite_list = [x for x in metabolite_list
                           if x.id not in self.metabolites]
        for x in metabolite_list:
            x._model = self
        self.metabolites += metabolite_list

        # from cameo ...
        for met in metabolite_list:
            if met.id not in self.solver.constraints:
                constraint = self.solver.interface.Constraint(
                    S.Zero, name=met.id, lb=0, ub=0)
                self.solver.add(constraint)

    def add_reaction(self, reaction):
        """Will add a cobra.Reaction object to the model, if
        reaction.id is not in self.reactions.

        reaction: A :class:`~cobra.core.Reaction` object

        """
        self.add_reactions([reaction])

    def add_reactions(self, reaction_list):
        """Will add a cobra.Reaction object to the model, if
        reaction.id is not in self.reactions.

        reaction_list: A list of :class:`~cobra.core.Reaction` objects

        """

        try:
            reaction_list = DictList(reaction_list)
        except TypeError:
            # This function really should not used for single reactions
            reaction_list = DictList([reaction_list])
            warn("Use add_reaction for single reactions")

        # Only add the reaction if one with the same ID is not already
        # present in the model.
        reactions_in_model = [
            i.id for i in reaction_list if self.reactions.has_id(
                i.id)]

        if len(reactions_in_model) > 0:
            raise Exception("Reactions already in the model: " +
                            ", ".join(reactions_in_model))

        # Add reactions. Also take care of genes and metabolites in the loop
        for reaction in reaction_list:
            reaction._model = self  # the reaction now points to the model
            # keys() is necessary because the dict will be modified during
            # the loop
            for metabolite in list(reaction._metabolites.keys()):
                # if the metabolite is not in the model, add it
                # should we be adding a copy instead.
                if not self.metabolites.has_id(metabolite.id):
                    self.metabolites.append(metabolite)
                    metabolite._model = self
                    # this should already be the case. Is it necessary?
                    metabolite._reaction = set([reaction])
                # A copy of the metabolite exists in the model, the reaction
                # needs to point to the metabolite in the model.
                else:
                    stoichiometry = reaction._metabolites.pop(metabolite)
                    model_metabolite = self.metabolites.get_by_id(
                        metabolite.id)
                    reaction._metabolites[model_metabolite] = stoichiometry
                    model_metabolite._reaction.add(reaction)

            for gene in list(reaction._genes):
                # If the gene is not in the model, add it
                if not self.genes.has_id(gene.id):
                    self.genes.append(gene)
                    gene._model = self
                    # this should already be the case. Is it necessary?
                    gene._reaction = set([reaction])
                # Otherwise, make the gene point to the one in the model
                else:
                    model_gene = self.genes.get_by_id(gene.id)
                    if model_gene is not gene:
                        reaction._dissociate_gene(gene)
                        reaction._associate_gene(model_gene)

        self.reactions += reaction_list

        # from cameo ...
        self._populate_solver(reaction_list)

    def _populate_solver(self, reaction_list, metabolite_list=None):
        """Populate attached solver with constraints and variables that model the provided reactions."""
        constraint_terms = AutoVivification()
        if metabolite_list is not None:
            for met in metabolite_list:
                constraint = self.solver.interface.Constraint(S.Zero, name=met.id, lb=0, ub=0)
                self.solver.add(constraint)

        for reaction in reaction_list:

            if reaction.reversibility:
                forward_variable = self.solver.interface.Variable(reaction._get_forward_id(), lb=0,
                                                                  ub=reaction._upper_bound)
                reverse_variable = self.solver.interface.Variable(reaction._get_reverse_id(), lb=0,
                                                                  ub=-1 * reaction._lower_bound)
            elif 0 == reaction.lower_bound and reaction.upper_bound == 0:
                forward_variable = self.solver.interface.Variable(reaction._get_forward_id(), lb=0, ub=0)
                reverse_variable = self.solver.interface.Variable(reaction._get_reverse_id(), lb=0, ub=0)
            elif reaction.lower_bound >= 0:
                forward_variable = self.solver.interface.Variable(reaction.id, lb=reaction._lower_bound,
                                                                  ub=reaction._upper_bound)
                reverse_variable = self.solver.interface.Variable(reaction._get_reverse_id(), lb=0, ub=0)
            elif reaction.upper_bound <= 0:
                forward_variable = self.solver.interface.Variable(reaction.id, lb=0, ub=0)
                reverse_variable = self.solver.interface.Variable(reaction._get_reverse_id(),
                                                                  lb=-1 * reaction._upper_bound,
                                                                  ub=-1 * reaction._lower_bound)

            self.solver.add(forward_variable)
            self.solver.add(reverse_variable)
            self.solver.update()

            for metabolite, coeff in six.iteritems(reaction.metabolites):
                if metabolite.id in self.solver.constraints:
                    constraint = self.solver.constraints[metabolite.id]
                else:
                    constraint = self.solver.interface.Constraint(S.Zero, name=metabolite.id, lb=0, ub=0)
                    self.solver.add(constraint, sloppy=True)

                constraint_terms[constraint][forward_variable] = coeff
                constraint_terms[constraint][reverse_variable] = -coeff

            objective_coeff = reaction._objective_coefficient
            if objective_coeff != 0.:
                if self.solver.objective is None:
                    self.solver.objective = self.solver.interface.Objective(0, direction='max')
                if self.solver.objective.direction == 'min':
                    self.solver.objective.direction = 'max'
                self.solver.objective.set_linear_coefficients(
                    {forward_variable: objective_coeff, reverse_variable: -objective_coeff})

        self.solver.update()
        for constraint, terms in six.iteritems(constraint_terms):
            constraint.set_linear_coefficients(terms)

    def to_array_based_model(self, deepcopy_model=False, **kwargs):
        """Makes a :class:`~cobra.core.ArrayBasedModel` from a cobra.Model which
        may be used to perform linear algebra operations with the
        stoichiomatric matrix.

        deepcopy_model: Boolean.  If False then the ArrayBasedModel points
        to the Model

        """
        from .ArrayBasedModel import ArrayBasedModel
        return ArrayBasedModel(self, deepcopy_model=deepcopy_model, **kwargs)

    def optimize(self, objective_sense='maximize', solution_type=Solution, **kwargs):
        r"""Optimize model using flux balance analysis

        objective_sense: 'maximize' or 'minimize'

        solver: 'glpk', 'cglpk', 'gurobi', 'cplex' or None

        quadratic_component: None or :class:`scipy.sparse.dok_matrix`
            The dimensions should be (n, n) where n is the number of reactions.

            This sets the quadratic component (Q) of the objective coefficient,
            adding :math:`\\frac{1}{2} v^T \cdot Q \cdot v` to the objective.

        tolerance_feasibility: Solver tolerance for feasibility.

        tolerance_markowitz: Solver threshold during pivot

        time_limit: Maximum solver time (in seconds)

        .. NOTE :: Only the most commonly used parameters are presented here.
                   Additional parameters for cobra.solvers may be available and
                   specified with the appropriate keyword argument.

        """
        if kwargs.get('solver', None) in ('cglpk', 'gurobi'):
            solution = optimize(self, objective_sense=objective_sense, **kwargs)
            self.solution = solution
            return solution

        else:  ## make the following honor the solver kwarg ...
            # from cameo ...
            self._timestamp_last_optimization = time.time()
            if objective_sense is not None:
                original_direction = self.solver.objective.direction
                self.solver.objective.direction = {'minimize': 'min', 'maximize': 'max'}[objective_sense]
                self.solver.optimize()
                self.solver.objective.direction = original_direction
            else:
                self.solver.optimize()
            solution = solution_type(self)
            self.solution = solution
            return solution

    def solve(self, solution_type=LazySolution, *args, **kwargs):
        """Optimize model.

        Parameters
        ----------
        solution_type : Solution or LazySolution, optional
            The type of solution that should be returned (defaults to LazySolution).

        Returns
        -------
        Solution or LazySolution
        """
        solution = self.optimize(solution_type=solution_type, *args, **kwargs)
        if solution.status is not 'optimal':
            raise exceptions._OPTLANG_TO_EXCEPTIONS_DICT.get(solution.status, SolveError)(
                'Solving model %s did not return an optimal solution. The returned solution status is "%s"' % (
                    self, solution.status))
        else:
            return solution

    def remove_reactions(self, reactions, delete=True,
                         remove_orphans=False):
        """remove reactions from the model

        reactions: [:class:`~cobra.core.Reaction.Reaction`] or [str]
            The reactions (or their id's) to remove

        delete: Boolean
            Whether or not the reactions should be deleted after removal.
            If the reactions are not deleted, those objects will be
            recreated with new metabolite and gene objects.

        remove_orphans: Boolean
            Remove orphaned genes and metabolites from the model as well

        """
        if isinstance(reactions, string_types) or hasattr(reactions, "id"):
            warn("need to pass in a list")
            reactions = [reactions]
        for reaction in reactions:
            try:
                reaction = self.reactions[self.reactions.index(reaction)]
            except ValueError:
                warn('%s not in %s' % (reaction, self))
            else:
                if delete:
                    reaction.delete(remove_orphans=remove_orphans)
                else:
                    reaction.remove_from_model(remove_orphans=remove_orphans)

    def repair(self, rebuild_index=True, rebuild_relationships=True):
        """Update all indexes and pointers in a model"""
        if rebuild_index:  # DictList indexes
            self.reactions._generate_index()
            self.metabolites._generate_index()
            self.genes._generate_index()
        if rebuild_relationships:
            for met in self.metabolites:
                met._reaction.clear()
            for gene in self.genes:
                gene._reaction.clear()
            for rxn in self.reactions:
                for met in rxn._metabolites:
                    met._reaction.add(rxn)
                for gene in rxn._genes:
                    gene._reaction.add(rxn)
        # point _model to self
        for l in (self.reactions, self.genes, self.metabolites):
            for e in l:
                e._model = self
        if self.solution is None:
            self.solution = Solution(None)
        return

    def change_objective(self, value, time_machine=None):
        """
        Changes the objective of the model to the given value. Allows
        passing a time machine to revert the change later
        """
        if time_machine is None:
            self.objective = value
        else:
            time_machine(do=partial(setattr, self, "objective", value),
                         undo=partial(setattr, self, "objective",
                                      self.objective))

    @property
    def objective(self):
        """The model objective."""
        # TODO: the following should check if the model objective actually contains only reactions
        return {reaction: reaction.objective_coefficient
                for reaction in self.reactions
                if reaction.objective_coefficient != 0}

    @objective.setter
    def objective(self, value):
        # are all these ways of setting the objective necessary?
        for reaction in self.reactions: # cobra does this / cameo doesn't
            reaction.objective_coefficient = 0.
        if isinstance(value, six.string_types):
            try:
                value = self.reactions.get_by_id(value)
            except KeyError:
                raise ValueError("No reaction with the id %s in the model"
                                 % value)
        if isinstance(value, int):
            value = self.reactions[value]
        if isinstance(value, Reaction):
            if value.model is not self:
                raise ValueError("%r does not belong to the model" % value)
            value.objective_coefficient = 1.
            self.solver.objective = self.solver.interface.Objective(
                value.flux_expression, sloppy=True)
        elif isinstance(value, self.solver.interface.Objective):
            self.solver.objective = value
        elif isinstance(value, sympy.Basic):
            self.solver.objective = self.solver.interface.Objective(
                value, sloppy=False)
        elif isinstance(value, (dict, list)):
            for item in value:
                if isinstance(item, int):
                    reaction = self.reactions[item]
                elif isinstance(item, six.string_types):
                    reaction = self.reactions.get_by_id(item)
                elif hasattr(item, 'id'):
                    reaction = self.reactions.get_by_id(item.id)
                else:
                    raise ValueError('item in iterable cannot be %s' %
                                     type(item))
                if isinstance(value, list):
                    reaction.objective_coefficient = 1
                if isinstance(value, dict):
                    reaction.objective_coefficient = value[item]
        # TODO(old): maybe the following should be allowed
        # elif isinstance(value, optlang.interface.Objective):
        # self.solver.objective = self.solver.interface.Objective.clone(value)
        else:
            raise TypeError('%r is not a valid objective for %r.' %
                            (value, self.solver))

    def summary(self, **kwargs):
        """Print a summary of the input and output fluxes of the model. This
        method requires the model to have been previously solved.

        threshold: float
            tolerance for determining if a flux is zero (not printed)

        fva: int or None
            Whether or not to calculate and report flux variability in the
            output summary

        floatfmt: string
            format method for floats, passed to tabulate. Default is '.3g'.

        """

        try:
            from ..flux_analysis.summary import model_summary
            return model_summary(self, **kwargs)
        except ImportError:
            warn('Summary methods require pandas/tabulate')

    @property
    def medium(self):
        """Current medium."""
        reaction_ids = []
        reaction_names = []
        lower_bounds = []
        upper_bounds = []
        for ex in self.exchanges:
            metabolite = list(ex.metabolites.keys())[0]
            coeff = ex.metabolites[metabolite]
            if coeff * ex.lower_bound > 0:
                reaction_ids.append(ex.id)
                reaction_names.append(ex.name)
                lower_bounds.append(ex.lower_bound)
                upper_bounds.append(ex.upper_bound)

        return DataFrame({'reaction_id': reaction_ids,
                          'reaction_name': reaction_names,
                          'lower_bound': lower_bounds,
                          'upper_bound': upper_bounds},
                         index=None, columns=['reaction_id', 'reaction_name', 'lower_bound', 'upper_bound'])

    @property
    def exchanges(self):
        """Exchange reactions in model.

        Reactions that either don't have products or substrates.
        """
        return [reaction for reaction in self.reactions if len(reaction.reactants) == 0 or len(reaction.products) == 0]
