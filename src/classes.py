from __future__ import division, unicode_literals, print_function

from pymatgen.core.structure import Structure
from pymatgen.core.lattice import Lattice
from pymatgen.core.composition import Composition
from pymatgen.core.periodic_table import Element
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.analysis.structure_matcher import ElementComparator
from pymatgen.phasediagram.maker import CompoundPhaseDiagram
from pymatgen.phasediagram.entries import PDEntry
from pymatgen.transformations.standard_transformations import RotationTransformation

# from abc import abstractmethod, ABCMeta
from _pyio import __metaclass__
# import collections.deque
from _collections import deque
import random  # TODO: need to make sure all random numbers from the same PRNG
import threading
from os import listdir
from os.path import isfile, join, exists
import numpy as np
from numpy import inf, Inf

'''
This module contains all the classes used by the algorithm.

TODO: should probably break this into several modules eventually.
'''

class IDGenerator(object):
    '''
    Generates successive integer ID numbers, starting from 1.
    
    This class is a singleton.
    '''
    
    def __init__(self):
        '''
        Creates an id generator.
        '''
        self.id = 0
    
    def makeID(self):
        '''
        Returns the next id number.
        '''
        self.id = self.id + 1
        return self.id



class Organism(object):
    '''
    An organism
    '''

    def __init__(self, structure, value=None, fitness=None, select_prob=None, isActive=False):
        '''
        Creates an organism
        
        Args:
            structure: The structure of this organism, as a pymatgen.core.structure.Structure
            
            composition: The composition of this organism, as a pymatgen.core.composition.Composition
            
            value: The objective function value of this organism, which is either the energy
                per atom (for fixed-composition search) or the distance from the current best
                convex hull (for phase diagram search).
                
            fitness: The fitness of this organism. Ranges from 0 to 1, including both endpoints.
            
            select_prob: The selection probability of this organism. Ranges from 0 to 1, including
                both endpoints.
                
            isActive: Whether this organism is currently part of the pool or initial population
        '''
        # initialize instance variables
        self.structure = structure
        self.composition = self.structure.composition
        self.value = value
        self.fitness = fitness
        self.select_prob = select_prob 
        self.isActive = isActive
        #  self._id = IDGenerator.makeID(); # unique id number for this organism. Should not be changed.
    
    # this keeps the id (sort of) immutable by causing an exception to be raised if the user tries to 
    # the set the id with org.id = some_id.
    @property
    def id(self):
        return self._id
    
    # TODO: maybe make setter methods for fitness and select_prob that check that they're between 0 and 1. 
    #    The Structure class has checks at initialization, so we don't need to check it again here.
    
    def rotateToPrincipalDirections(self):
        '''
        Rotates the organism's structure into the principal directions. That is, a is parallel to the Cartesian x-axis, 
        b lies in the Cartesian x-y plane and the z-component of c is positive.
        
        Note: this method doesn't change the fractional coordinates of the sites. However, the Cartesian coordinates may be changed
        '''
        # rotate about the z-axis to align a vertically with the x-axis
        rotation = RotationTransformation([0, 0, 1], 180 - (180/np.pi)*np.arctan2(self.structure.lattice.matrix[0][1], self.structure.lattice.matrix[0][0]))
        self.structure = rotation.apply_transformation(self.structure)
        # rotate about the y-axis to make a parallel to the x-axis
        rotation = RotationTransformation([0, 1, 0], 180 - (180/np.pi)*np.arctan2(self.structure.lattice.matrix[0][2], self.structure.lattice.matrix[0][0]))
        self.structure = rotation.apply_transformation(self.structure)
        # rotate about the x-axis to make b lie in the x-y plane
        rotation = RotationTransformation([1, 0, 0], 180 - (180/np.pi)*np.arctan2(self.structure.lattice.matrix[1][2], self.structure.lattice.matrix[1][1]))
        self.structure = rotation.apply_transformation(self.structure)
        # make sure they are all pointing in positive directions
        if self.structure.lattice.matrix[0][0] < 0:
            # rotate about y-axis to make a positive
            rotation = RotationTransformation([0, 1, 0], 180)
            self.structure = rotation.apply_transformation(self.structure)
        if self.structure.lattice.matrix[1][1] < 0:
            # rotate about x-axis to make b positive
            rotation = RotationTransformation([1, 0, 0], 180)
            self.structure = rotation.apply_transformation(self.structure)
        if self.structure.lattice.matrix[2][2] < 0:
            # mirror c across the x-y plane to make it positive - have to build a new lattice to do this
            # the components of a
            ax = self.structure.lattice.matrix[0][0]
            ay = self.structure.lattice.matrix[0][1]
            az = self.structure.lattice.matrix[0][2]
            # the components of b
            bx = self.structure.lattice.matrix[1][0]
            by = self.structure.lattice.matrix[1][1]
            bz = self.structure.lattice.matrix[1][2]
            # the components of c
            cx = self.structure.lattice.matrix[2][0]
            cy = self.structure.lattice.matrix[2][1]
            cz = -1*self.structure.lattice.matrix[2][2]
            
            self.structure.modify_lattice(Lattice([[ax, ay, az], [bx, by, bz], [cx, cy, cz]]))
        
        
    def reduceSheetCell(self):
        '''
        Applies Niggli cell reduction to a sheet structure. 
        
        The idea is to make c vertical and add lots of vertical vacuum so that the standard reduction algorithm only changes the a and b lattice vectors
        '''
        # rotate into principal directions
        self.rotateToPrincipalDirections()
        # get the species and their Cartesian coordinates
        species = self.structure.species
        cartesian_coords = self.structure.cart_coords
        # get the non-zero components of the a and b lattice vectors, and the vertical component of the c lattice vector
        ax = self.structure.lattice.matrix[0][0]
        bx = self.structure.lattice.matrix[1][0]
        by = self.structure.lattice.matrix[1][1]
        cz = self.structure.lattice.matrix[2][2]
        # make a new lattice with a ton of vertical vacuum (add 100 Angstroms)
        padded_lattice = Lattice([[ax, 0.0, 0.0], [bx, by, 0.0], [0.0, 0.0, cz + 100]])
        # make a new structure with the padded lattice and Cartesian coordinates
        padded_structure = Structure(padded_lattice, species, cartesian_coords, coords_are_cartesian=True)
        # do cell reduction on the padded structure (the c lattice vector should still be parallel to z, and a and b should still lie in x-y plane)
        reduced_structure = padded_structure.get_reduced_structure()
        # unpad the reduced structure
        rspecies = reduced_structure.species
        rcartesian_coords = reduced_structure.cart_coords
        rax = reduced_structure.lattice.matrix[0][0]
        ray = reduced_structure.lattice.matrix[0][1]
        rbx = reduced_structure.lattice.matrix[1][0]
        rby = reduced_structure.lattice.matrix[1][1]
        unpadded_lattice = Lattice([[rax, ray, 0.0], [rbx, rby, 0.0], [0.0, 0.0, cz]])
        
        self.structure = Structure(unpadded_lattice, rspecies, rcartesian_coords, coords_are_cartesian=True)
        
        
    def getLayerThickness(self):
        '''
        Returns the layer thickness of a sheet structure, which is the maximum vertical distance between atoms in the cell.
        
        Assumes that the organism has already been rotated into the principal directions, and that plane of the sheet is parallel to the a-b facet.
        '''
        # get the Cartesian coordinates of the atoms in the structure
        cart_coords = self.structure.cart_coords
        # find the largest and smallest vertical coordinates
        maxz = -Inf
        minz = Inf
        for coord in cart_coords:
            if coord[2] > maxz:
                maxz = coord[2]
            if coord[2] < minz:
                minz = coord[2]
        # compute the layer thickness
        layer_thickness = maxz - minz
        return layer_thickness
    
    
    def getWireDiameter(self):
        '''
        Returns the diameter of a wire structure
        
        Assumes that the organism has already been put into wire format (whatever that means)
        '''
        # TODO: implement me
        print("Please implement me.")
        
    
    def getClusterDiameter(self):
        '''
        Returns the diameter of a cluster structure
        
        Assumes that the organism has already been put into cluster format (whatever that means)
        '''
        # TODO: implement me
        print("Please implement me.")
        
            
            


class Pool(object):
    '''
    Pool to hold all the organisms that are currently candidates for selection to become parents.
    
    Composed of two parts: a promotion set containing the best few organisms, and a queue containing
        rest of the organisms in the pool.
        
    This class is a singleton.
    '''

    def __init__(self, pool_params_dict, initial_population, selection_probability_dict):
        '''
        Creates a pool of organisms
        
        Args:
            pool_params_dict: a dictionary containing the pool parameters: poolSize, numPromoted
        
            initial_population: a list of organism.Organism's that comprise the initial population. They must
                already have had their energies calculated.
            
            selection_probability_dict: a dictionary containing the parameters needed to calculating selection 
                probabilities: numParents and selectionPower
        '''
        # TODO: implement me
        # 1. calculate the fitnesses of the organisms in the initial population, based on their
        #    objective function values
        # 2. calculate the selection probabilities of the organisms, based on their fitnesses
        # 3. put the best few of them in the promotion set (exact number determined from input)
        # 4. put the rest in the queue. The order doesn't matter, since they'll all get thrown away
        #    at the same time
        # I think I can just use a list for the promotion set 
        # Use a deque for the queue
        self.promotionSet = [] # TODO: should be set to a list of the best few orgs
        self.queue = deque() # TODO: should be a deque containing the rest of the orgs
        self.selectionDist = [] # TODO: the parameters for the selection distribution
        self.numAdds = 0 # the number of organisms added to the pool (excluding the initial population)
    
    
    def addOrganism(self, org):
        '''
        Adds a new organism to the pool, and also to whole_pop.
        
        If the new organism better than one of the orgs currently in the promotion set, then it is added 
        to the promotion set, and the worst org in the promotion set is moved to the back of the queue. 
        Otherwise, the new org is just appended to the back of the queue.
        
        Args:
            org: the organism.Organism to add.
        '''
        # TODO: implement me. Look at deque methods...
        # 1. If doing a pd search, will need to transform value from epa to distance from current best convex hull
        # 2. Once value is updated (if necessary), decide whether to place in promotion set or queue, based on org values
        # 3. Add organism to whole_pop (whole_pop.append(org))
        # 4. Set org.isActive = True
        
        self.numAdds = self.numAdds + 1
        
    
    def replaceOrganism(self, old_org, new_org):
        '''
        Replaces an organism in the pool with a new organism. The new organism has the same location in the pool
        as the old one.
        
        Precondition: the old_org is a member of the current pool.
        
        Args:
            old_org: the organism in the pool to replace
            new_org: the new organism to replace the old one
        '''
        # TODO: implement me
        # 1. determine if old_org is in promotion set or queue
        # 2. do the replacement
        # 3. set old_org.isActive = False and newOrg.isActive = True
        # 3. if the new_org is either the best or worst in the pool, will need to update fitnesses and selection probs
    
    
    def calculateFitnesses(self):
        '''
        Calculates and assigns fitnesses to all organisms in the pool.
        
        Precondition: the organisms in the pool all have valid values
        '''
        # TODO: implement me. 
        # There might be some tricks to speed this up, like:
        #    always keeping track of the best and worst values in the pool, 
        #    so we don't have to search for them each time
        #
        #    might only have to update the fitness of the newest addition to the pool, 
        #    if the best and worst values didn't change when it was added.
        
    
    def calculateSelectionProbs(self):
        '''
        Calculates and assigns selection probabilities to all the organisms in the pool.
        
        Precondition: the organisms in the pool all have valid values.
        '''
        # TODO: implement me
        # some of the same tricks as in calculateFitnesses possible here too
        
        
    def toList(self):
        '''
        Returns a list containing all the organisms in the pool.
        '''
        # TODO: implement me
        
        

class Variation(object):
    '''
    A general variation object for creating offspring organisms from parent organism(s)
    
    Not meant to be instantiated, but rather subclassed by particular Variations, like Mating,
    Mutation, etc.
    '''
    
    def doVariation(self):
        '''
        Creates an offspring organism from parent organism(s).
        
        Returns an organism.
        '''
        raise NotImplementedError("Please implement this method.")
    
    
    def getSelectionProb(self, variation_parameters):
        '''
        Returns the selection probability of this variation, as a float between 0 and 1
        
        Args:
            variation_parameters: a dictionary containing the parameters for the variation
        '''
        return variation_parameters["selectProb"]
    
    
    def selectParents(self, n, pool):
        '''
        Selects n distinct organisms from the pool.
        
        Returns a list containing n organisms.
        
        Args:
            n: how many organisms to select from the pool
            pool: the current pool of organisms
            
        Precondition: all the organisms in the pool have been assigned selection probabilities.
        '''
        # TODO: implement me



class Mating(Variation):
    '''
    A mating operator.
    
    This class is a singleton.
    '''

    def __init__(self, mating_params):
        '''
        Creates a mating operator
        
        Args:
            
            mating_params: The parameters for doing the mating operation, as a dictionary.
                
        '''
        # TODO: initialize the instance variables using the values in the dict. Maybe just keeping a 
        # copy of the dict as an instance variable is enough...
    
    def doVariation(self):
        '''
        Performs the mating operation, as described in ref. TODO
        
        Returns the resulting offspring as an organism.Organism
        '''
        # TODO: implement me
        # 1. select two parent organisms from the pool - selectParents(2, pool)
        # 2. combine the two parents to make an offspring structure, and return a new offspring organism 
        #    with that structure
        
        
        
class Mutation(Variation):
    '''
    A mutation operator.
    
    This class is a singleton.
    '''
    
    def __init__(self, mutation_params):
        '''
        Creates a mutation operator
        
        Args:
        
            mutation_params: The parameters for doing the mutation operation, as a dict.
        '''   
        # TODO: initialize the instance variables using the values in the dict. Maybe just keeping a
        #    copy of the dict as an instance variable is enough...
        
    
    def doVariation(self):
        '''
        Performs the mutation operation, as described in ref. TODO
        
        Returns the resulting offspring as an organism.Organism
        ''' 
        # TODO: implement me
        # 1. select a parent organism from the pool - selectParents(1, pool)
        # 2. do the mutation to make an offspring structure, and return a new offspring organism with 
        #    that structure
   
        

class Permutation(Variation):
    '''
    A permutation operator.
    
    This class is a singleton.
    '''
    
    def __init__(self, permutation_params):
        '''
        Creates a permutation operator
        
        Args:
        
        permutation_params: The parameters for doing the permutation operation, as a dict.
        '''
        # TODO: initialize the instance variables using the values in the dict. Maybe just keeping a
        #    copy of the dict as an instance variable is enough...
    
    
    def doVariation(self):
        '''
        Performs the permutation operation, as described in ref. TODO
        
        Returns the resulting offspring as an organism.Organism
        '''
        # TODO: implement me
        # 1. select a parent organism from the pool - selectParents(1, pool)
        # 2. do the permutation to make an offspring structure, and return an offspring organism with 
        #    that structure
        
        

class NumStoichsMut(Variation):
    '''
    An operator that creates an offspring organism by mutating the number of stoichiometries' worth of atoms 
    in the parent organism.
    
    This class is a singleton.
    '''
    
    def __init__(self, numstoichsmut_params):
        '''
        Creates a NumStoichsMut operator
        
        Args:
        
        numstoichsmut_params: The parameters for doing the numstoichsmut operation, as a dict.
        '''
        # TODO: initialize the instance variables using the values in the dict. Maybe just keeping a
        #    copy of the dict as an instance variable is enough...
    
    def doVariation(self, parent):
        '''
        Performs the numstoichsmut operation, as described in ref. TODO
        
        Returns the resulting offspring as an organism.Organism
        '''
        # TODO: implement me
        # 1. select a parent organism from the pool - selectParents(1, pool)
        # 2. do the numstoichsmutation to make an offspring structure, and return an offspring organism 
        #    with that structure
        
        
        
class Geometry(object):
    '''
    Represents the geometry data, including any geometry-specific constraints (max_size, etc.)
    
    This class is a singleton
    '''
    
    def __init__(self, geometry_parameters):
        '''
        Creates a geometry object
        
        Args:
            geometry_parameters: a dictionary of parameters
        '''
        # default values
        self.default_shape = 'bulk'
        self.default_max_size = inf
        self.default_padding = 10 # this will only be used for non-bulk shapes
        
        # if entire Geometry block was set to default or left blank
        if geometry_parameters == None or geometry_parameters == 'default':
            self.shape = self.default_shape
            self.max_size = self.default_max_size
            self.padding = None
        else:     
            # check each one and see if it's been left blank or set to default, or not included at all 
            if 'shape' in geometry_parameters:
                # if no shape was given, assume bulk and set everything to default
                if geometry_parameters['shape'] == None or geometry_parameters['shape'] == 'default':
                    self.shape = self.default_shape
                    self.max_size = self.default_max_size
                    self.padding = None
                else:
                    self.shape = geometry_parameters['shape']
                    # set max size, and check if was left blank or set to None or 'default'
                    if 'max_size' in geometry_parameters:
                        if geometry_parameters['max_size'] == None or geometry_parameters['max_size'] == 'default':
                            self.max_size = self.default_max_size
                        else:
                            self.max_size = geometry_parameters['max_size']
                    else:
                        self.max_size = self.default_max_size
                    # set padding, and check if was left blank or set to None or 'default'
                    if 'padding' in geometry_parameters:
                        if geometry_parameters['padding'] == None or geometry_parameters['padding'] == 'default':
                            self.padding = self.default_padding
                        else:
                            self.padding = geometry_parameters['padding']
                    else:
                        self.padding = self.default_padding
            # if shape field was missing, assume bulk and set default values
            else:
                self.shape = self.default_shape
                self.max_size = self.default_max_size
                self.padding = None
                    
                        
                        
                
    def pad(self, structure):
        '''
        Makes an organism's structure conform to the required shape. For sheet, wire and cluster geometries, this 
        means adding vacuum padding to the cell. For bulk, the structure is unchanged. Used to pad a structure prior to 
        an energy calculation.
        
        Returns a structure that has been modified to conform to the shape (most likely padded with vacuum).
        
        Args:
            structure: the structure of an organism, as a pymatgen.core.structure.Structure object
        '''
        # rotate structure into principal directions
        # Call other methods based on the value of self.shape
        if self.shape == 'sheet':
            return self.padSheet(structure)
        elif self.shape == 'wire':
            return self.padWire(structure)
        elif self.shape == 'cluster':
            return self.padCluster(structure)
        else:
            return structure
        
    def padSheet(self, structure):
        '''
        Adds vertical vacuum padding to a sheet, and makes the c-lattice vector normal to the plane of the sheet.
        
        Returns a sheet structure that has been padded with vacuum.
        
        Args:
            structure: the structure of an organism, as a pymatgen.core.structure.Structure object
        '''
        # TODO: implement me
        # 1. rotate structure to the principal directions
        #         TODO: see if pymatgen has a method for this
        # 2. replace c with it's vertical component (make it normal to plane of the sheet) (make sure atomic positions are preserved)
        
        # 3. reduce c such that it's equal to the layer thickness (max vertical distance between atoms in the cell)
        # 4. add padding to c
        # 5. return padded structure
        
    def padWire(self, structure):
        '''
        Adds vacuum padding around a wire.
        
        Returns a wire structure that has been padded with vacuum.
        
        Args:
            structure: the structure of an organism, as a pymatgen.core.structure.Structure object
        '''
        # TODO: implement me
    
    def padCluster(self, structure):
        '''
        Adds vacuum padding around a cluster.
        
        Returns a cluster structure that has been padded with vacuum.
        
        Args:
            structure: the structure of an organism, as a pymatgen.core.structure.Structure object
        '''
        # TODO: implement me
    
    
    def unpad(self, structure):
        '''
        Removes vacuum padding to return an organism's structure to a form used by the variations.
        
        Returns a structure that has had the vacuum padding removed.
        
        Args:
            structure: the structure of an organism, as a pymatgen.core.structure.Structure object
        '''
        # rotate structure into principal directions
        # Call other methods based on the value of self.shape
        if self.shape == 'sheet':
            return self.unpadSheet(structure)
        elif self.shape == 'wire':
            return self.unpadWire(structure)
        elif self.shape == 'cluster':
            return self.unpadCluster(structure)
        else:
            return structure
        
    
    def unpadSheet(self, structure):
        '''
        Removes vertical vacuum padding from a sheet.
        
        Returns a sheet structure with the vertical vacuum padding removed
        
        Precondition: the sheet structure is represented with the c-lattice vector perpendicular to the plane of the sheet
        
        Args:
            structure: the structure of an organism, as a pymatgen.core.structure.Structure object
        '''
        # TODO: implement me
        
    def unpadWire(self, structure):
        '''
        Removes vacuum padding around a wire.
        
        Returns a wire structure with the vertical vacuum padding removed
        
        Args:
            structure: the structure of an organism, as a pymatgen.core.structure.Structure object
        '''
        # TODO: implement me
        
    def unpadCluster(self, structure):
        '''
        Removes vacuum padding around a cluster.
        
        Returns a wire structure with the vertical vacuum padding removed
        
        Args:
            structure: the structure of an organism, as a pymatgen.core.structure.Structure object
        '''
        # TODO: implement me
        
       
class CompositionSpace(object):
    '''
    Represents the composition space to be searched by the algorithm.
    
    This class is a singleton.
    '''
    
    def __init__(self, endpoints):
        '''
        Creates a CompositionSpace object, which is list of pymatgen.core.composition.Composition objects
        
        Args:
            endpoints: the list dictionaries mapping element symbols to amounts, with each one representing a compositions
        '''
        for i in range(0, len(endpoints)):
            endpoints[i] = Composition(endpoints[i])
            
        self.endpoints = endpoints
        
        # for now, let's have the objective live here
        self.objective_function = self.inferObjectiveFunction()
    
    
    def inferObjectiveFunction(self):
        '''
        Infers the objective function (energy per atom or phase diagram) based on the composition space
        
        Returns either "epa" or "pd"
        '''
        # if only one composition, then it must be an epa search
        if len(self.endpoints) == 1:
            return "epa"
        # otherwise, compare all the compositions and see if any of them are different
        else:
            for point in self.endpoints:
                for next_point in self.endpoints:
                    if not point.almost_equals(next_point, 0.0, 0.0):
                        return "pd"
        # should only get here if there are multiple identical compositions in end_points (which would be weird)
        return "epa"
                    
        
            
       
        
class OrganismCreator(object):
    '''
    Creates organisms for the initial population
    
    Not meant to be instantiated, but rather subclassed by particular Creators, like RandomOrganismCreator
    or PoscarsOrganismCreator.
    
    TODO: is this even necessary? All it specifies is that a creator should have a createOrganism method that returns an organism or None. 
    '''
    
    def createOrganism(self):
        '''
        Creates an organism for the initial population.
        
        Returns an organism, or None if one could not be created
        
        Args:
            TODO: think about what data this method needs (e.g. objective function data, etc.) and add it 
                to the argument list.
        '''
        raise NotImplementedError("Please implement this method.")
        


class RandomOrganismCreator(OrganismCreator):
    '''
    Creates random organisms for the initial population
    '''
    def __init__(self, random_org_parameters, composition_space):
        '''
        Creates a RandomOrganismCreator.
        
        Args:
            random_org_parameters: the parameters for generating random organisms
            
            composition_space: a CompositionSpace object   
        '''
        # the default number of random organisms to make
        if composition_space.objective_function == 'epa':
            self.default_number = 30
        elif composition_space.objective_function == 'pd':
            self.default_number = 40
        # the default volume scaling behavior
        self.default_volume = 'from_elemental_densities'
        
        # if entire random_org_parameters is None or 'default', then set to defaults
        if random_org_parameters == None or random_org_parameters == 'default':
            self.number = self.default_number
            self.volume = self.default_volume
        
        # otherwise, parse the parameters and set to defaults if necessary
        else:
            if 'number' in random_org_parameters:
                if random_org_parameters['number'] == None or random_org_parameters['number'] == 'default':
                    self.number = self.default_number
                else:
                    self.number = random_org_parameters['number']
            else:
                # if no 'number' tag, then just use the default
                self.number = self.default_number
            
            # get the volume to scale them to 
            if 'volume' in random_org_parameters:
                if random_org_parameters['volume'] == None or random_org_parameters['volume'] == 'default':
                    self.volume = self.default_volume
                else:
                    self.volume = random_org_parameters['volume']
            else:
                # if no 'volume' tag given, then just do the default
                self.volume = self.default_volume
                
        # variables to keep track of how many have been made, when to stop, and if this creator is finished   
        # for a random organism creator, num_made is defined as the number of organisms made that have been added to the initial population 
        self.num_made = 0
        self.is_successes_based = True
        self.is_finished = False
    
    def createOrganism(self, composition_space, constraints):
        '''
        Creates a random organism for the initial population.
        
        Returns a random organism, or None if an error was encountered during volume scaling
        
        Args:
            composition_space: a CompositionSpace object
            
            constraints: a Constraints object 
        '''
        # make three random lattice vectors that satisfy the length constraints
        a = constraints.min_lattice_length + random.random()*(constraints.max_lattice_length - constraints.min_lattice_length)
        b = constraints.min_lattice_length + random.random()*(constraints.max_lattice_length - constraints.min_lattice_length)
        c = constraints.min_lattice_length + random.random()*(constraints.max_lattice_length - constraints.min_lattice_length)
        
        # make three random lattice angles that satisfy the angle constraints
        alpha = constraints.min_lattice_angle + random.random()*(constraints.max_lattice_angle - constraints.min_lattice_angle)
        beta = constraints.min_lattice_angle + random.random()*(constraints.max_lattice_angle - constraints.min_lattice_angle)
        gamma = constraints.min_lattice_angle + random.random()*(constraints.max_lattice_angle - constraints.min_lattice_angle)
        
        # build the random lattice
        random_lattice = Lattice.from_parameters(a, b, c, alpha, beta, gamma)
        
        # get a list of elements for the random organism
        if composition_space.objective_function == 'epa':
            reduced_formula = composition_space.endpoints[0].reduced_composition
            num_atoms_in_formula = reduced_formula.num_atoms
            max_num_formulas = int(constraints.max_num_atoms/num_atoms_in_formula)
            # get a random number of formula units
            random_num_formulas = random.randint(1, max_num_formulas)
            # add the right number of each element
            elements = []
            for element in reduced_formula:
                for _ in range(random_num_formulas*reduced_formula[element]):
                    elements.append(element) 
        elif composition_space.objective_function == 'pd':
            # TODO: this doesn't ensure the organism will be in the composition space. If it's not, it will just fail development, but there might be a better way...
            num_atoms = random.randint(constraints.min_num_atoms, constraints.max_num_atoms)
            allowed_elements = constraints.get_all_elements(composition_space)
            elements = []
            for _ in range(num_atoms):
                elements.append(random.choice(allowed_elements))
        
        # for each element, generate a set of random fractional coordinates
        # TODO: this doesn't ensure the structure will satisfy the per-species mids, and in fact most won't. It's ok because they'll just fail development, but there might be a better way...
        random_coordinates = []
        for _ in range(num_atoms):
            random_coordinates.append([random.random(), random.random(), random.random()])
        
        # make a random organism from the random lattice, random species, and random coordinates
        random_structure = Structure(random_lattice, elements, random_coordinates)
        random_org = Organism(random_structure)
        
        # optionally scale the volume
        if self.volume == 'from_elemental_densities':
            # scale the volume to the weighted average of the densities of the elemental constituents
            # TODO: this would break if pymatgen doesn't have a density for a particular element
            
            # compute volumes per atom (in Angstrom^3) of each element in the random organism
            reduced_composition = random_org.structure.composition.reduced_composition
            volumes_per_atom = {}
            for element in reduced_composition:
                # physical properties and conversion factors
                atomic_mass = float(element.atomic_mass) # in amu
                mass_conversion_factor = 1.660539040e-27 # converts amu to kg
                density = float(element.density_of_solid) # in kg/m^3
                length_conversion_factor = 1.0e10 # converts meters to Angstroms 
            
                # compute the volume (in Angstrom^3) per atom of this element
                # take the log of the product to prevent numerical issues
                log_volume_per_atom = np.log(mass_conversion_factor) + np.log(atomic_mass) - np.log(density) + 3.0*np.log(length_conversion_factor)
                volume_per_atom = np.exp(log_volume_per_atom)                
                volumes_per_atom[element] = volume_per_atom
        
            # compute the desired volume per atom by taking the weighted average of the volumes per atom of the constituent elements
            weighted_sum = 0
            for element in reduced_composition:
                # the number of this type of element times it's calculated volume per atom
                weighted_sum = weighted_sum + reduced_composition[element]*volumes_per_atom[element]
        
            # normalize by the total number of atoms to get the weighted average
            mean_vpa = weighted_sum/reduced_composition.num_atoms
        
            # scale the volume of the random organism to satisfy the computed mean volume per atom
            # TODO: sometimes this doesn't work. It can either scale the volume to some huge number, or else volume scaling just fails and lattice vectors are assigned nan
            #       it looks like the second error is caused by a divide-by-zero in the routine pymatgen calls to scale the volume
            #       the if statement below is to catch these cases, by I should probably contact materials project about it...
            random_org.structure.scale_lattice(mean_vpa*len(random_org.structure.sites))
            if str(random_org.structure.lattice.a) == 'nan' or random_org.structure.lattice.a > 100:
                return None          
        
        elif self.volume == 'random':
            # no volume scaling
            pass
        
        else:
            # scale to the given volume per atom
            random_org.structure.scale_lattice(self.volume*len(random_org.structure.sites(self)))  
        
        # return the scaled random organism
        return random_org
    
    def updateStatus(self):
        '''
        Increments num_made, and if necessary, updates is_finished
        '''
        self.num_made = self.num_made + 1
        if self.num_made == self.number:
            self.is_finished = True
        
        


class FileOrganismCreator(OrganismCreator):
    '''
    Creates organisms from files (poscar or cif) for the initial population.
    '''
    def __init__(self, path_to_folder):
        '''
        Creates a FileOrganismCreator.
        
        Args:
            path_to_folder: the path to the folder containing the files from which to make organisms
                            Precondition: the folder exists and contains files
        '''
        # all the files in the given folder
        self.path_to_folder = path_to_folder
        self.files = [f for f in listdir(self.path_to_folder) if isfile(join(self.path_to_folder, f))]
      
        # variables to keep track of how many have been made, when to stop, and if this creator is finished   
        # for a file organism creator, num_made is defined as the number of attempts to make organisms from files (usually the number of files provided)
        self.num_made = 0
        self.is_successes_based = False
        self.is_finished = False
    
    def createOrganism(self):
        '''
        Creates an organism for the initial population from a poscar or cif file. 
        
        Returns an organism, or None if one could not be created
        '''
        # update status each time the method is called, since this is an attempts-based creator
        self.updateStatus()
        # TODO: This is kind of annoying. Maybe contact pymatgen and ask if they can add support for files ending in .POSCAR instead of only files starting with POSCAR 
        if self.files[self.num_made - 1].endswith('.cif') or self.files[self.num_made - 1].startswith('POSCAR'):
            try:
                new_struct = Structure.from_file(str(self.path_to_folder) + "/" + str(self.files[self.num_made - 1]))
            # return None if a structure couldn't be read from a file
            except ValueError:
                return None
            return Organism(new_struct)
        else:
            print('Invalid file extension: file must end in .cif or begin with POSCAR')
            return None
        
    def updateStatus(self):
        '''
        Increments num_made, and if necessary, updates is_finished
        '''
        self.num_made = self.num_made + 1
        if self.num_made == len(self.files):
            self.is_finished = True
        



class RedundancyGuard(object):
    '''
    A redundancy guard.
    
    This is a singleton class.
    '''
    
    def __init__(self, redundancy_parameters):
        '''
        Creates a redundancy guard.
        
        Args:
            redundancy parameters: a dictionary of parameters
        '''
        # TODO: are these sensible defaults?
        # default lattice length tolerance, in fractional coordinates (pymatgen uses 0.2 as default...)
        self.default_lattice_length_tol = 0.1 
        # default lattice angle tolerance, in degrees (pymatgen uses 5 as default...)
        self.default_lattice_angle_tol = 2 
        # default site tolerance, in fraction of average free length per atom (pymatgen uses 0.3 as default...)
        self.default_site_tol = 0.1
        # whether to transform to primitive cells before comparing
        self.default_use_primitive_cell = True
        # whether to check if structures are equivalent to supercells of each other
        self.default_attempt_supercell = True
        # the d-value interval
        self.default_d_value = 0
        
        # parse the parameters, and set to defaults if necessary
        if redundancy_parameters == None or redundancy_parameters == 'default':
            self.set_all_to_defaults()
        else:
            # check each flag to see if it's been included, and if so, whether it has been set to default or left blank
            # lattice length tolerance
            if 'lattice_length_tol' in redundancy_parameters:
                if redundancy_parameters['lattice_length_tol'] == None or redundancy_parameters['lattice_length_tol'] == 'default':
                    self.lattice_length_tol = self.default_lattice_length_tol
                else:
                    self.lattice_length_tol = redundancy_parameters['lattice_length_tol']
            else:
                self.lattice_length_tol = self.default_lattice_length_tol
                
            # lattice angle tolerance
            if 'lattice_angle_tol' in redundancy_parameters:
                if redundancy_parameters['lattice_angle_tol'] == None or redundancy_parameters['lattice_angle_tol'] == 'default':
                    self.lattice_angle_to = self.default_lattice_angle_tol
                else:
                    self.lattice_angle_to = redundancy_parameters['lattice_angle_tol']
            else:
                self.lattice_angle_to = self.default_lattice_angle_tol
                
            # site tolerance
            if 'site_tol' in redundancy_parameters:
                if redundancy_parameters['site_tol'] == None or redundancy_parameters['site_tol'] == 'default':
                    self.site_tol = self.default_site_tol
                else:
                    self.site_tol = redundancy_parameters['site_tol']
            else:
                self.site_tol = self.default_site_tol
            
            # whether to use primitive cells
            if 'use_primitive_cell' in redundancy_parameters:
                if redundancy_parameters['use_primitive_cell'] == None or redundancy_parameters['use_primitive_cell'] == 'default':
                    self.use_primitive_cell = self.default_use_primitive_cell
                else:
                    self.use_primitive_cell = redundancy_parameters['use_primitive_cell']
            else:
                self.use_primitive_cell = self.default_use_primitive_cell
            
            # whether to try matching supercells
            if 'attempt_supercell' in redundancy_parameters:
                if redundancy_parameters['attempt_supercell'] == None or redundancy_parameters['attempt_supercell'] == 'default':
                    self.attempt_supercell = self.default_attempt_supercell
                else:
                    self.attempt_supercell = redundancy_parameters['attempt_supercell']
            else:
                self.attempt_supercell = self.default_attempt_supercell
                
            # d-value
            if 'd_value' in redundancy_parameters:
                if redundancy_parameters['d_value'] == None or redundancy_parameters['d_value'] == 'default':
                    self.d_value = self.default_d_value
                else:
                    self.d_value = redundancy_parameters['d_value']
            else:
                self.d_value = self.default_d_value
        
        # make the StructureMatcher object
        # The first False is to prevent the matcher from scaling the volumes, and the second False is to prevent subset matching
        self.structure_matcher = StructureMatcher(self.lattice_length_tol, self.site_tol, self.lattice_angle_to, self.use_primitive_cell, False, self.attempt_supercell, False, ElementComparator())
        
    def set_all_to_defaults(self):
        '''
        Sets all the redundancy parameters to default values
        '''
        self.lattice_length_tol = self.default_lattice_length_tol
        self.lattice_angle_to = self.default_lattice_angle_tol
        self.site_tol = self.default_site_tol
        self.use_primitive_cell = self.default_use_primitive_cell
        self.attempt_supercell = self.default_attempt_supercell
        self.d_value = self.default_d_value
        
    def checkRedundancy(self, new_organism, whole_pop):
        '''
        Checks for redundancy, both structural and if specified, value (d-value)
        
        Returns the organism with which new_organism is redundant, or None if no redundancy
        
        TODO: make failure messages more informative - include organism number, etc.
        
        Args:
            new_organism: the organism to check for redundancy
            
            whole_pop: the list containing all organisms to check against
        '''
        for organism in whole_pop:
            # check if their structures match
            if self.structure_matcher.fit(new_organism.structure, organism.structure):
                print("Organism failed structural redundancy")
                return organism
            # if specified and both have values, check if their values match within d-value
            if self.d_value != None and new_organism.value != None and organism.value != None:
                if abs(new_organism.value - organism.value) < self.d_value:
                    print("Organism failed value redundancy")
                    return organism    
        # should only get here if no organisms are redundant with the new organism
        return None    
        


class Constraints(object):
    '''
    Represents the general constraints imposed on structures considered by the algorithm. 
    '''
    
    def __init__(self, constraints_parameters, composition_space):
        '''
        Sets the general constraints imposed on structures. Assigns default values if needed.
        
        Args:
            constraints_parameters: a dictionary of parameters
            
            composition_space: a CompositionSpace object describing the composition space to be searched
        '''
        # default values
        self.default_min_num_atoms = 2
        self.default_max_num_atoms = 20
        self.default_min_lattice_length = 0.5
        self.default_max_lattice_length = 20
        self.default_min_lattice_angle = 40
        self.default_max_lattice_angle = 140
        self.default_allow_endpoints = True
        
        # set defaults if constraints_parameters equals 'default' or None
        if constraints_parameters == None or constraints_parameters == 'default':
            self.set_all_to_defaults(composition_space)
        else:
            # check each flag to see if it's been included, and if so, whether it has been set to default or left blank
            # min number of atoms
            if 'min_num_atoms' in constraints_parameters:
                if constraints_parameters['min_num_atoms'] == None or constraints_parameters['min_num_atoms'] == 'default':
                    self.min_num_atoms = self.default_min_num_atoms
                else:
                    self.min_num_atoms = constraints_parameters['min_num_atoms']
            else:
                self.min_num_atoms = self.default_min_num_atoms    
                
            # max number of atoms   
            if 'max_num_atoms' in constraints_parameters:
                if constraints_parameters['max_num_atoms'] == None or constraints_parameters['max_num_atoms'] == 'default':
                    self.max_num_atoms = self.default_max_num_atoms
                else:
                    self.max_num_atoms = constraints_parameters['max_num_atoms']
            else:
                self.max_num_atoms = self.default_max_num_atoms    
                
            # min lattice length    
            if 'min_lattice_length' in constraints_parameters:
                if constraints_parameters['min_lattice_length'] == None or constraints_parameters['min_lattice_length'] == 'default':
                    self.min_lattice_length = self.default_min_lattice_length
                else:
                    self.min_lattice_length = constraints_parameters['min_lattice_length']
            else:
                self.min_lattice_length = self.default_min_lattice_length     
                 
            # max lattice length    
            if 'max_lattice_length' in constraints_parameters:
                if constraints_parameters['max_lattice_length'] == None or constraints_parameters['max_lattice_length'] == 'default':
                    self.max_lattice_length = self.default_max_lattice_length
                else:
                    self.max_lattice_length = constraints_parameters['max_lattice_length']
            else:
                self.max_lattice_length = self.default_max_lattice_length 
             
            # min lattice angle    
            if 'min_lattice_angle' in constraints_parameters:
                if constraints_parameters['min_lattice_angle'] == None or constraints_parameters['min_lattice_angle'] == 'default':
                    self.min_lattice_angle = self.default_min_lattice_angle
                else:
                    self.min_lattice_angle = constraints_parameters['min_lattice_angle']
            else:
                self.min_lattice_angle = self.default_min_lattice_angle
             
            # max lattice angle    
            if 'max_lattice_angle' in constraints_parameters:
                if constraints_parameters['max_lattice_angle'] == None or constraints_parameters['max_lattice_angle'] == 'default':
                    self.max_lattice_angle = self.default_max_lattice_angle
                else:
                    self.max_lattice_angle = constraints_parameters['max_lattice_angle']
            else:
                self.max_lattice_angle = self.default_max_lattice_angle    
             
            # allowing endpoints   
            if 'allow_endpoints' in constraints_parameters:
                if constraints_parameters['allow_endpoints'] == None or constraints_parameters['allow_endpoints'] == 'default':
                    self.allow_endpoints = self.default_allow_endpoints
                else:
                    self.allow_endpoints = constraints_parameters['allow_endpoints']
            else:
                self.allow_endpoints = self.default_allow_endpoints
                  
            # the per-species min interatomic distances
            if 'per_species_mids' in constraints_parameters:
                if constraints_parameters['per_species_mids'] != None and constraints_parameters['per_species_mids'] != 'default':
                    self.per_species_mids = constraints_parameters['per_species_mids'] 
                    # check each pair that's been specified to see if it needs a default mid
                    for key in self.per_species_mids:
                        if self.per_species_mids[key] == None or self.per_species_mids[key] == 'default':
                            elements = key.split()
                            radius1 = Element(elements[0]).atomic_radius
                            radius2 = Element(elements[1]).atomic_radius
                            self.per_species_mids[key] = 0.8*(radius1 + radius2)
                    # check to see if any pairs are missing, and if so, add them and set to default values
                    self.set_some_mids_to_defaults(composition_space)
                # if the per_species_mids block has been left blank or set to default, then set all the pairs to defaults
                else:
                    self.set_all_mids_to_defaults(composition_space)
            # if the per_species_mids block wasn't set in the input file, then set all the pairs to defaults
            else:
                self.set_all_mids_to_defaults(composition_space)
            
                
                
    def set_all_to_defaults(self, composition_space):
        '''
        Sets all general constraints (those in Constraints block of input file) to default values
        
        Args:
            composition_space: the composition space object
        '''
        self.min_num_atoms = self.default_min_num_atoms
        self.max_num_atoms = self.default_max_num_atoms
        self.min_lattice_length = self.default_min_lattice_length
        self.max_lattice_length = self.default_max_lattice_length
        self.min_lattice_angle = self.default_min_lattice_angle
        self.max_lattice_angle = self.default_max_lattice_angle
        self.allow_endpoints = self.default_allow_endpoints
        self.set_all_mids_to_defaults(composition_space) 
        
        
    def set_all_mids_to_defaults(self, composition_space):
        '''
        Sets all the per-species mids to default values based on atomic radii
        
        Args:
            composition_space: the composition space object
        '''
        # get each element type from the composition_space object
        elements = self.get_all_elements(composition_space)   
        # compute the per_species_mids based on atomic radii
        self.per_species_mids = {}
        for i in range(0, len(elements)):
            for j in range(i, len(elements)):
                self.per_species_mids[str(elements[i].symbol + " " + elements[j].symbol)] = 0.8*(elements[i].atomic_radius + elements[j].atomic_radius)
        
    
    def set_some_mids_to_defaults(self, composition_space):
        '''
        Compares all the possible pairs of elements to what is contained in self.per_species_mids. If any pairs are missing, adds them with default values.
        
        Args:
            composition_space: a CompositionSpace object
        ''' 
        # get each element type from the composition_space object
        elements = self.get_all_elements(composition_space)
        # list to hold lists of missing pairs
        missing_pairs = []
        # scan through every possible pair to check if it's already included in self.per_species_mids
        for i in range(0, len(elements)):
            for j in range(i, len(elements)):
                # check both orders
                test_key1 = elements[i].symbol + " " + elements[j].symbol
                test_key2 = elements[j].symbol + " " + elements[i].symbol
                if test_key1 not in self.per_species_mids and test_key2 not in self.per_species_mids:
                    missing_pairs.append(test_key1)
                        
        # calculate the per species mids for all the missing pairs and add them to self.per_species_mids
        for pair in missing_pairs:
            p = pair.split()
            self.per_species_mids[str(pair)] = 0.8*(Element(p[0]).atomic_radius + Element(p[1]).atomic_radius)
            
            
        
    def get_all_elements(self, composition_space):
        '''
        Returns a list of all the elements (as pymatgen.core.periodic_table.Element objects) that are in the composition space
        
         Args:
            composition_space: the composition space object
        '''
        # get each element type from the composition_space object
        elements = []
        for point in composition_space.endpoints:
            for key in point:
                elements.append(key)
        # remove duplicates from the list of elements
        elements = list(set(elements)) 
        return elements
        
        


class Development(object):
    '''
    A development object is used to develop an organism before evaluating its energy or adding it
    to the pool. Doesn't do redundancy checking.
    
    This is a singleton class.
    '''
    
    def __init__(self, niggli, scale_density):
        '''
        Creates a Development object.
        
        Args:
            niggli: a boolean indicating whether or not to do Niggli cell reduction
            
            scale_density: a boolean indicating whether or not to scale the density
        '''
        self.niggli = niggli
        self.scale_density = scale_density
        
    
    def develop(self, organism, composition_space, constraints, geometry, pool):
        '''
        Develops an organism.
        
        Returns the developed organism, or None if the organism failed development
        
        TODO: make failure messages more informative - include organism number, etc.
        TODO: it might make more sense to return a flag indicating whether the organism survived development, since this method modifies the organism...
        
        Args:
            organism: the organism to develop
            
            composition_space: a CompositionSpace object
            
            constraints: a Constraints object
            
            geometry: a Geometry object
            
            pool: the current pool. If this method is called before a pool exists (e.g., while making the initial population)
                  then pass None as the argument instead.
        '''
        # check max num atoms constraint
        if len(organism.structure.sites) > constraints.max_num_atoms:
            print("Organism failed max num atoms constraint - rejecting")
            return None
            
        # check min num atoms constraint
        if len(organism.structure.sites) < constraints.min_num_atoms:
            print("Organism failed min num atoms constraint - rejecting")
            return None
        
        # check if the organism has the right composition for fixed-composition searches
        if composition_space.objective_function == "epa":
            if not composition_space.endpoints[0].almost_equals(organism.composition):
                print("Organism has incorrect composition - rejecting")
                return None
        
        # check if the organism is in the composition space for phase-diagram searches
        # This is kind of hacky, but the idea is to use the CompoundPhaseDiagram.transform_entries method to do 
        # the heavy lifting of determining whether a composition lies in the composition space 
        elif composition_space.objective_function == "pd":
            # cast all the endpoints to PDEntries, and just make up some energies
            pdentries = []
            for endpoint in composition_space.endpoints:
                pdentries.append(PDEntry(endpoint, -10))
            # also cast the organism we want to check to a PDEntry
            pdentries.append(PDEntry(organism.composition, -10))
            # construct the CompoundPhaseDiagram object that we'll use to check if the organism is in the composition space
            composition_checker = CompoundPhaseDiagram(pdentries, composition_space.endpoints)
            # use the CompoundPhaseDiagram to check if the organism is in the composition space by seeing how many entries it returns
            if len(composition_checker.transform_entries(pdentries, composition_space.endpoints)[0]) == len(composition_space.endpoints):
                print("Organism not in composition space - rejecting")
                return None
            else:
                # check the endpoints if specified and if we're not making the initial population
                if constraints.allow_endpoints == False and pool != None:
                    for endpoint in composition_space.endpoints:
                        if endpoint.almost_equals(organism.composition):
                            print("Organism at an endpoint - rejecting")
                            return None
                        
        # optionally do Niggli cell reduction
        if self.niggli:
            if geometry.shape == "bulk":
                # do normal Niggli cell reduction
                organism.structure = organism.structure.get_reduced_structure()
            elif geometry.shape == "sheet":
                # do the sheet Niggli cell redution
                organism.reduceSheetCell()     
            # TODO: implement cell reduction for other geometries here if needed (doesn't makes sense for wires or clusters)
            
        # rotate the structure into the principal directions
        organism.rotateToPrincipalDirections()
                     
        # optionally scale the density to the average of the densities of the organisms in the promotion set 
        # TODO: test this once Pool has been implemented
        if self.scale_density and composition_space.objective_function == "epa" and pool != None and organism.value == None:
            # get average volume per atom of the organisms in the promotion set
            vpa_sum = 0
            for org in pool.promotionSet:
                vpa_sum = vpa_sum + org.structure.volume/len(org.structure.sites)
            vpa_mean = vpa_sum/len(pool.promotionSet)
            # compute the new volume per atom
            num_atoms = len(organism.structure.sites)
            new_vol = vpa_mean*num_atoms
            # scale to the new volume
            organism.structure.scale_lattice(new_vol)
            
        # check the max and min lattice length constraints
        lengths = organism.structure.lattice.abc
        for length in lengths:
            if length > constraints.max_lattice_length:
                print("Organism failed max lattice length constraint - rejecting")
                return None
            elif length < constraints.min_lattice_length:
                print("Organism failed min lattice length constraint - rejecting")
                return None
            
        # check the max and min lattice angle constraints
        angles = organism.structure.lattice.angles
        for angle in angles:
            if angle > constraints.max_lattice_angle:
                print("Organism failed max lattice angle constraint - rejecting")
                return None
            elif angle < constraints.min_lattice_angle:
                print("Organism failed min lattice angle constraint - rejecting")
                return None
            
        # check the per-species minimum interatomic distance constraints
        species_symbols = organism.structure.symbol_set
        for site in organism.structure.sites:
            for species_symbol in species_symbols:
                # get the mid for this particular pair. We don't know the ordering in per_species_mids, so try both
                test_key1 = species_symbol + " " + site.specie.symbol
                test_key2 = site.specie.symbol + " " + species_symbol
                if test_key1 in constraints.per_species_mids:
                    mid = constraints.per_species_mids[test_key1]
                elif test_key2 in constraints.per_species_mids:
                    mid = constraints.per_species_mids[test_key2]  
                # get all the sites within a sphere of radius mid centered on the current site
                neighbors = organism.structure.get_neighbors(site, mid)
                # check each neighbor in the sphere to see if it has the forbidden type
                for neighbor in neighbors:
                    if neighbor[0].specie.symbol == species_symbol:
                        print("Organism failed per-species minimum interatomic distance constraint - rejecting")
                        return None
            
        # check the max size constraint for non-bulk geometries
        if geometry.shape == 'sheet':
            if organism.getLayerThickness() > geometry.max_size:
                print("Organism failed max size constraint - rejecting")
                return None
        elif geometry.shape == 'wire':
            if organism.getWireDiameter() > geometry.max_size:
                print("Organism failed max size constraint - rejecting")
                return None
        elif geometry.shape == 'cluster':
            if organism.getClusterDiameter() > geometry.max_size:
                print("Organism failed max size constraint - rejecting")
                return None
        # TODO: any other geometry-specific constraints checks go here
        
        # return the organism if it survived
        return organism
                
                
            


class OffspringGenerator(object):
    '''
    Used to generate offspring structures
    '''
    
    def __init__(self, variations, development, redundancy_guard, num_tries_limit):
        '''
        Args:
            variations: a list of Variation objects 
            
            development: the Development object (for cell reduction and structure constraints)
            
            redundancy_guard: the redundancyGuard object 
            
            num_tries_limit: the max number of times to attempt creating an offspring organism from a given variation
                before giving up and trying a different variation.
        '''
        self.variation = variations
        self.development = development
        self.redundancy_guard = redundancy_guard
        self.num_tries_limit = num_tries_limit
        
        
    def makeOffspringOrganism(self, pool, whole_pop):
        '''
        Generates a valid offspring organism using the variations and adds it to whole_pop.
        
        Returns an unrelaxed offspring organism.
        
        Args:
            pool: the current Pool
            
            whole_pop: the list containing all the orgs seen so far (both relaxed an unrelaxed)
        '''
        tried_variations = []
        while(len(self.variations) > len(tried_variations)):
            variation = self.selectVariation(tried_variations)
            num_tries = 0
            while (num_tries < self.num_tries_limit):
                offspring = variation.doVariation()
                offspring = self.development.develop(offspring)
                if (offspring != None) and (self.redundancy_guard.checkRedundancy(offspring, whole_pop) == None):
                    whole_pop.append(offspring)
                    return offspring
                else:
                    num_tries = num_tries + 1
            tried_variations.append(variation)
        print("Could not make valid offspring organism with any Variation.") # This point should never be reached
        
    
    def selectVariation(self, tried_variations):
        '''
        Selects a variation that hasn't been tried yet based on their selection probabilities
        
        Args:
            tried_variations: list of Variations that have already been unsuccessfully tried
        '''
    # TODO: implement me
    #
    #    while(true):
    #        variation = random_selection(variations)    # choose one randomly based on their selection probs.
    #        if (variation not in tried_variations):
    #            return variation
    
        
    
class EnergyCalculator(object):
    '''
    Handles calculating the energy of organisms.
    
    Not meant to be instantiated, but rather subclassed by particular Calculators, like VaspEnergyCalculator
    or GulpEnergyCalculator.
    '''
    
    def doEnergyCalculation(self, org):
        '''
        Calculates the energy of an organism
        
        Returns an organism that has been parsed from the output files of the energy code, or None if the calculation 
        failed. Does not do development or redundancy checking.
        
        Args:
            org: the organism whose energy we want to calculate
        '''
        raise NotImplementedError("Please implement this method.")
        # TODO: think about how to impelement this. There are two main parts: preparing for the calculation (writing
        #    input files), and actually submitting it, by calling an external script or something. Once the calculation
        #    is finished, we need to develop the relaxed organism, add it to the whole_pop list, and add it to the
        #    waiting_queue.
        #
        #    All this should be done on it's own thread, so that multiple energy calculations can be running at once.
        #    It might be best to handle the threads inside the the method (not sure)
        #
        #    The goal is be able to call EnergyCalculator.doEnergyCalcualtion(unrelaxed_organism) and then have the
        #    control flow immediately return (i.e. not having to wait for the method to finish)
        #
        #    Note: when the energy calculation finishes, this method will need to have access to the current versions
        #        of the whole_pop list and the waiting_queue. Not sure best way to do that...
        
        # set up the energy calc (prepare input files, etc.)
        # do the energy calc
        # if it finished correctly: 
        #    relaxed_org = development.develop(relaxed_org) it and append updated org to the waiting queue
        #    if (relaxed_org != None):
        #        waiting_queue.append(relaxed_org)
        #    else:
        #        print("failed constraints") # say which one
        # else:
        #    print("Energy calculation failed. Discarding org") 
        
        

class VaspEnergyCalculator(object):
    '''
    Calculates the energy of an organism using VASP.
    '''
    
    def __init__(self, vasp_code_params):
        '''
        Args:
            vasp_code_params: the parameters needed for preparing a vasp calculation (INCAR, KPOINTS, POTCAR files)
        '''
        # TODO: implement me. Just keeping the paths to the input files should be enough
    
    
    def doEnergyCalculation(self, org):
         '''
        Calculates the energy of an organism using VASP
        
        Returns an organism that has been parsed from the output files of the energy code, or None if the calculation 
        failed. Does not do development or redundancy checking.
        
        Args:
            org: the organism whose energy we want to calculate
        '''
        # TODO: implement me
        # 1. prepare input files for calculation
        # 2. submit calculation (by running external script)
        # 3. when external script returns, parse organism from the energy code output files
        


class InitialPopulation():
    '''
    The initial population of organisms
    '''
    
    def __init__(self, whole_pop):
        '''
        Args:
            whole_pop: the list containing the organisms seen by the algorithm for redundancy checking
        '''
        self.initial_population = []
    
    
    def addOrganism(self, org, whole_pop):
         '''
        Adds a relaxed organism to the initial population and updates whole_pop.
        
        Args:
            org: the organism whose energy we want to calculate
            
            whole_pop: the list containing all the organisms that the algorithm has submitted for energy calculations
        '''
      #  initial_population.append(org)
      #  org.isActive = True
      #  whole_pop.append(org)
      
      
    def replaceOrganism(self, old_org, new_org):
        '''
        Replaces an organism in the initial population with a new organism.
        
        Precondition: the old_org is a current member of the initial population
        
        Args:
            old_org: the organism in the initial population to replace
            new_org: the new organism to replace the old one
        '''
        # TODO: implement me
        # 1. do the replacement
        # 2. set old_org.isActive = False and newOrg.isActive = True
        # 3. whole_pop.append(new_org)
    

        
        
        
        
########## area for casual testing ########## 

# make a structure object
#lattice = [[1,0.5,0], [0.5,1,0], [0,0,-1]]
#species = ["C", "Si"]
#coordinates = [[0.25,0.25,0.25],[0.75,0.75,0.75]]
#structure1 = Structure(lattice, species, coordinates)

# make an organism object
#org1 = Organism(structure1)

#print(org1.structure.lattice)

#org1.rotateToPrincipalDirections()
#print("")
#print(org1.structure.lattice)

#print(org1.structure)
#print(org1.fitness)
#print(org1.id)

#org1.id = 6
#print(org1.id)

########## end of testing area ##########    


'''
One way to store the parameters is in several groups, one for each related group of parameters

Possible groups include

    variations, which has four subgroups: mutation, mating, permutation, numstoichsmut
    
    initial population settings
    
    redundancy guard settings
    
    termination criteria
    
    objective function info
    
    things needed by the algorithm at all times, like
        - number of calcs to run concurrently
        - pool size
        - the selection probability function
        - volume scaling
        - niggli cell reduction


I think where possible, the data should be stored inside the objects that need it, like the variation objects.

Idea: read in the input file with minimal processing (just a string or something), then have each object parse 
the data it needs out of that string when it is initialized. Some of it might just go into lists or something.

Maybe use dictionaries to store the data

Ok, start by making a list of all the input file options, and think about how to logically divide that into 
dictionaries. Let's worry about parsing from the input file later, including what format it should be in.

Optional flags [Current value]:
   --help : displays this message with parameters' default values
   --verbosity n : verbosity of output [4]
Genetic Algorithm
   --t runTitle : name the run
   --outDir : specify the output directory
   --keepTempFiles <true|false>
   --saveStateEachIter <true|false>
   --popSize <n> : use a non-initial population size of n
   --promotion <n> : promote n best structures (or the whole CH) to next gen
   --compositionSpace <numElements> <Sym>*  :     System composition for phase diagram search
                   or <numElements> <Sym>* <amount of element1> <amount of element2> etc.
   --optimizeDensity <weight of adaptation> <num orgs to avg. over>
   --useRedundancyGuard <wholePopulation|perGeneration|both> <atomic misfit> <lattice misfit> <angle misfit> <use PBCs?>
   --useSurrogateModel <gulp header file> <potentials_specification file> <refit frequency>
   --endgameNumGens <n>
   --useNiggliReducedCell <true|false>
   --use2DNiggliReducedCell <true|false>
   --useSubstrate <true|false> <path to substrate POSCAR file
   --writeHartkeFile <boolean>
   --colorOutput <boolean>
   
Initial Population
   --initialPopulation <num> random givenVol <volumeperatom>
   --initialPopulation <num> random randomVol
   --initialPopulation <num> poscars <directory>
   --initialPopulation <num> manual
   --initialPopulation <num> units <numMols> <numAtoms_1>...<numAtoms_n> (<symbol_i> <x_i> <y_i> <z_i>)+ <numUnits_1>...<numUnits_n> <targetDensity> <densityTol> <unitsOnly?>
   --initialPopulation <num> supercell a b c maxll minll maxla minla maxh maxna minna <randomsocreator args>
   
Objective Functions
   --objectiveFunction cluster <padding length> <other obj fcn args from below...>
   --objectiveFunction surface <padding length> <other obj fcn args from below...>
   --objectiveFunction substrate <padding length> <other obj fcn args from below...>
   --objectiveFunction <epa/pd> gulp <gulp header file> <gulp potential file> <cautious?> <species needing a shell>
   --objectiveFunction <epa/pd> vasp <cautious?> <kpoints> <incar> <element potcar>+ 
   --objectiveFunction <epa/pd> ohmms <header> <footer> <cautious?>
   --objectiveFunction <epa/pd> lammps <potlFile> <units> <relax box?>
   --objectiveFunction <epa/pd> castep <cautious?> <kpointSpacing> <pressure> <paramFile> <element potcar>+ 
   --objectiveFunction <epa/pd> avogadro <avog header file>
   --objectiveFunction <epa/pd> dlpoly <loc> <potl>
   --objectiveFunction <epa/pd> mopac <execpath>
   --objectiveFunction <epa/pd> dftpp <dftpp_inputs> <cautious?> <element ppFile.fhi>*
   --objectiveFunction <epa/pd> generic
   --parallelize <numCalcsInParallel> <minPopSize>
   
Variation Algorithms
   --variation <percentage> <percentage> slicer <thicknessMean> <thicknessSigma> <majorShiftFrac> <minorShiftFrac> <maxAmplitude> <maxFreq> <growParents?> <doublingProb>
   --variation <percentage> <percentage> structureMut <rate> <sigmaAtoms> <sigmaLattice>
   --variation <percentage> <percentage> permutation <meanSwaps> <sigmaSwaps> <pairsToSwap (e.g. Mn-O)>
   --variation <percentage> <percentage> numStoichsMut <meanNumAtoms> <sigmaNumAtoms>
   
Selection Algorithms
   --selection probDist <numParents> <selectionPower>
   
Convergence Criteria
   --convergenceCriterion maxFunctionEvals <n>
   --convergenceCriterion maxNumGens <n>
   --convergenceCriterion maxNumGensWOImpr <n> <dValue>
   --convergenceCriterion valueAchieved <maximum acceptable energy>
   --convergenceCriterion foundStructure <CIF filename> <rGuard misfits>
   
Hard Constraints
   --minInteratomicDistance d : minimum interatomic distance (Angstroms)
   --perSpeciesMID <symbol1> <symbol2> <distance>
   --maxLatticeLength d : maximum lattice vector length (Angstroms)
   --minLatticeLength d : minimum lattice vector length (Angstroms)
   --maxLatticeAngle d : maximum lattice angle (Degrees)
   --minLatticeAngle d : minimum lattice angle (Degrees)
   --maxCellHeight d : maximum height of cell in z-direction
   --maxNumAtoms n
   --minNumAtoms n
   --minNumSpecies n
   --doNonnegativityConstraint <boolean>
   --dValue x : discard organisms within a value of x of each other
   
   
   
   
   Variations:         # section
       Mating:         # subsection
           percentage
           thicknessMean
           thicknessSigma
           majorShiftFrac
           minorShiftFrac
           growParents
           doublingProb
        Mutation:
            percentage
            fracPerturbed
            sigmaAtoms
            sigmaLattice
        Permutation:
            percentage
            meanSwaps
            sigmaSwaps
            pairsToSwap (probably a list of pairs or something)
        NumStoichsMut:
            percentage
            meanNumAtoms
            sigmaNumAtoms
        
    Selection:
        numParents
        selectionPower
        
    Constraints:
        minInteratomicDistances (probably a dict of pairs and distances or something)
        maxLatticeLength
        minLatticeLength
        maxLatticeAngle
        minLatticeAngle
        maxClusterDiameter
        maxWireDiameter
        maxLayerThickness
        maxNumAtoms
        minNumAtoms
        minNumSpecies
        doNonnegativityConstraint (maybe don't need this?)
        dValue
        
    Convergence Criteria:
        maxFunctionEvals
        maybe others
   
   Objective Function:
       objectiveFunction <epa|pd>
       geometry <bulk|sheet|wire|cluster>
           # these should be ignored if geometry is bulk
           padding # how much vacuum to add (around cluster/wire/sheet)
           maxSize # how big it can be (diameter for cluster and wire, thickness for sheet. measured from atom-to-atom)
       energyCode <vasp|gulp|others>
       energyFiles (depends on energyCode. Not sure best way to handle this...)
        
'''
