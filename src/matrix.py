import numpy as np
import pandas as pd
import itertools
from typing import NoReturn, List, Tuple
import os

# TODO: Maybe think of a better way of organizing sample versus taxonomy metadata. 

class Matrix():

    def __init__(self, df:pd.DataFrame=None) -> NoReturn:

        if df is not None:
            self.matrix = df.values
            self.row_labels = df.index.values
            self.col_labels = df.columns.values

    def __getitem__(self, i):
        return self.matrix[i]        

    def __len__(self):
        return len(self.matrix)

    def transpose(self):
        '''Transpose the underlying data.'''

        row_labels, col_labels = self.row_labels, self.col_labels
        self.col_labels = row_labels
        self.row_labels = col_labels
        self.matrix = self.matrix.T
    
    def shape(self) -> Tuple[int, int]:
        '''Returns the shape of the underlying matrix.'''
        return self.matrix.shape


class DistanceMatrix(Matrix):

    def __init__(self, df:pd.DataFrame=None, metric:str=None):
        '''Initialize a DistanceMatrix object.'''
        super().__init__(df=df) # Initialize the parent class.
        self.metric = metric


class CountMatrix(Matrix):
    '''A matrix which contains observation counts in each cell. '''

    def __init__(self, df:pd.DataFrame=None):
        '''Initialize a CountMatrix object.'''
        super().__init__(df=df) # Initialize the parent class.
        # Remove any empty columns
        self.filter_empty_cols()

    def get_chi_squared_distance_matrix(self) -> DistanceMatrix:
        '''Compute the chi-squared distance matrix between rows. 
        Used formula from https://link.springer.com/referenceworkentry/10.1007/978-0-387-32833-1_53.'''

        P = self.matrix / np.sum(self.matrix) # Get the relative frequency table by dividing entries by the grand total. 
        nrows, ncols = P.shape # Store number of rows and columns. 

        # Define row and column vector totals. 
        pi, pj = np.sum(P, axis=1, keepdims=True), np.sum(P, axis=0, keepdims=True)
        self.pi, self.pj = pi, pj # Store the summed up vectors in the matrix. 

        D = np.zeros((nrows, nrows)) # Initialize an empty distance matrix. 
        for a in range(P.shape[0]): # Iterate over the rows.
            rows = P
            row_a = rows[a][np.newaxis, :] # Grab the primary row for this iteration. Add a new axis for broadcasting, so it is [[x1, x2, ...]].
            d = np.square(row_a - rows) # Should have the same dimensions as P. 
            assert d.shape == P.shape, f'matrices.Matrix.get_chi_squared_distance_matrix: The vector d should be shape {P.shape}. Instead, shape is {d.shape}.'
            d = d / pj # Divide by the column totals. 
            d = np.sum(d, axis=1) # Sum up over the columns. This collapses the dimension. 
            d = np.sqrt(d) # Take the square root of the sum. 
            assert len(d) == nrows, f'matrices.Matrix.get_chi_squared_distance_matrix: The vector d should be size {nrows}. Instead, shape is {d.shape}.'
            D[a] = d # Store the computed distances in the matrix. 

        df = pd.DataFrame(D, columns=self.row_labels, index=self.row_labels)
        return DistanceMatrix(df=df, metric='chi-squared') 

    def get_bray_curtis_distance_matrix(self) -> DistanceMatrix:
        '''Calculate the Bray-Curtis similarity score for each pair of samples in the
        AsvMatrix object.'''

        # Vectorizing sped this up from ~2 minutes to ~4 seconds!
        D = np.zeros((len(self.row_labels), len(self.row_labels))) # Initialize an empty distance matrix. 
        s = self.matrix.sum(axis=1) # Compute the total counts in each sample. 
        for i in range(len(self.row_labels)):
            x = self.matrix[i] # Grab the primary sample.
            # If axis = 1, apply_along_axis will loop through each row and implement the function to the row. 
            c = np.apply_along_axis(lambda y : np.where(np.less(y, x), y, x), 1, self.matrix).sum(axis=1)
            D[i] = 1 - (2 * c) / (s + s[i])

        df = pd.DataFrame(D, columns=self.row_labels, index=self.row_labels)
        return DistanceMatrix(df, metric='bray-curtis')

    def to_df(self):
        '''Convert the matrix to a pandas DataFrame.'''
        return pd.DataFrame(self.matrix, columns=self.col_labels, index=self.row_labels)
    
    def get_metadata(self, field:str) -> pd.Series:
        '''Extract information from a particular field in the metadata attribute.'''
        # Some checks to make sure the flux data is present. 
        assert self.metadata is not None, 'CountMatrix.get_metadata: There is no metadata stored in the CountMatrix object.'
        assert field in self.metadata.columns, f'CountMatrix.get_metadata: There is no field {field} in the CountMatrix metadata.'

        # The metadata table contains an entry for each ASV group. This reduces the metadata to one entry per sample. 
        sample_metadata = self.metadata[['serial_code', field]].drop_duplicates(ignore_index=True)
        # Extract the data from the metadata table.
        return sample_metadata[field]

    def get_metadata_fields(self) -> List[str]:
        '''Return a list of the fields contained in the underlying metadata.'''
        assert self.metadata is not None, 'CountMatrix.get_metadata_fields: There is no metadata stored in the CountMatrix object.'
        return list(self.metadata.columns)

    def filter_empty_cols(self):
        '''Remove empty columns from the matrix. Empty columns can occur when a subset of the total
        data is loaded, and not all ASVs or Taxonomical categories are represented.'''

        non_empty_idxs = np.sum(self.matrix, axis=0) > 0

        self.col_labels = self.col_labels[non_empty_idxs]
        self.matrix = self.matrix[:, non_empty_idxs]

    def filter_read_depth(self, min_depth:int):
        '''Filter the matrix so that only samples with more than the specified number of reads are kept.
        
        :param min_depth: The minimum depth requirement for keeping a sample in the AsvMatrix. 
        '''
        depths = np.sum(self.matrix, axis=1)
        idxs = depths >= min_depth
        print(f'matrices.CountMatrix.filter_read_depth: Discarding {len(self.matrix) - np.sum(idxs)} samples with read depth less than {min_depth}.')

        self.matrix = self.matrix[idxs]
        self.row_labels = self.row_labels[idxs]
        # If metadata is present, make sure to drop filtered samples. 
        if self.metadata is not None:
            self.metadata = self.metadata[self.metadata.serial_code.isin(self.row_labels)]

    def sample(self, i:int, n:int, species_count_only:bool=False):

        s = self.matrix[i] # Get the sample from the matrix. 
        
        # It doesn't make sense to sample from an array of relative abundances, I think. 
        assert self.normalized == False, 'matrix.AsvMatrix.sample: The AsvMatrix has already been normalized.'
        assert n <= np.sum(s), f'matrix.AsvMatrix.sample: The sample size must be no greater than the total number of observations ({np.sum(s)}).' 
        
        s = np.repeat(self.col_labels, s) # Convert the sample to an array of ASVs where each ASV is repeated the number of times it appears in the sample.
        s = np.random.choice(s, n, replace=False) # Sample from the array of ASV labels. 

        if species_count_only:
            # For plotting rarefaction curves, it is much faster to return only the number of unique species.
            return len(np.unique(s))
        else:
            # This uses numpy broadcasting to generate an n-dimensional array of boolean values for each asv, indicating if it matches the element in sample. 
            # Basically converts sample from an array of ASV labels to an array of counts, with indices corresponding to self.col_labels.
            s = np.sum(self.col_labels[:, np.newaxis] == s, axis=1).ravel()
            return s
        

class TaxonomyMatrix(CountMatrix):
    '''A lower-resolution version of the AsvMatrix. The columns of this matrix correspond to taxonomical categories
    of a specified level, and the rows are samples. Each cell contains the number of observations in the sample of organisms
    which belong to the taxonomical category.'''

    def __init__(self, df:pd.DataFrame=None, metadata:pd.DataFrame=None, level:str='phylum'):
        '''Initialize a TaxonomyMatrix.'''

        super().__init__(df=df)

        # The taxonomical level of the columns.
        self.metadata = metadata 
        self.level = level


class AsvMatrix(CountMatrix):
    '''An object for working with ASV tables, which are matrices of counts where each row corresponds
    to a sample and each column is an ASV group.'''

    def __init__(self, df:pd.DataFrame, metadata:pd.DataFrame=None):
        '''Initialize an AsvMatrix object.
        
        :param df: A pandas DataFrame containing, at minimum, columns serial_code (the sample ID), count, and asv.
        '''
        # Can we assume that all ASVs are present in every sample, but with a count of zero? I think yes, by looking
        # at the data file, but I should probably add an explicit check. 

        super().__init__(df=df) # Initialize the parent class. 

        self.metadata = metadata # Store the metadata. 
        self.normalized = False

    def get_taxonomy_matrix(self, level:str='phylum') -> TaxonomyMatrix:
        '''Merge the ASVs by taxonomy at the specified taxonomical level.'''
        assert self.metadata is not None, 'matrices.AsvMatrix.get_taxonomy_matrix: No metadata present in AsvMatrix object.'

        # Create a DataFrame where the rows are ASV groups and columns are samples (transpose of typical ASV count matrix).
        df = pd.DataFrame(self.matrix.T, index=self.col_labels, columns=self.row_labels)
        df['asv'] = df.index # Set an ASV column for merging. 
        # Extract the relevant taxonomy information from the metadata. 
        taxonomy_data = self.metadata[['asv', level]]
        df = df.merge(taxonomy_data, on='asv') # Combine the taxonomy metadata with the count matrix. 
        df = df.groupby(level).sum() # Group by taxonomy and sum up by sample. 
        df = df.drop(columns='asv') # Drop the ASV column. 

        return TaxonomyMatrix(df=df.transpose(), metadata=self.metadata, level=level)
    