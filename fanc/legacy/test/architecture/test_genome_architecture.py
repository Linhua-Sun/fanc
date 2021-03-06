from __future__ import division
from fanc.architecture.architecture import calculateondemand
from fanc.architecture.genome_architecture import MatrixArchitecturalRegionFeature, VectorArchitecturalRegionFeature
from fanc.data.genomic import GenomicRegion, Node, Edge
import tables as t
import types
import numpy as np


class VAF(VectorArchitecturalRegionFeature):
    """
    This only exists so we can instantiate a VectorArchitecturalRegionFeature
    for testing.
    """
    def __init__(self, file_name=None, mode='a', data_fields=None,
                 regions=None, data=None, _table_name_data='region_data',
                 tmpdir=None):
        VectorArchitecturalRegionFeature.__init__(self, file_name=file_name, mode=mode, data_fields=data_fields,
                                                  regions=regions, data=data, _table_name_data=_table_name_data,
                                                  tmpdir=tmpdir)

    def _calculate(self, *args, **kwargs):
        self.add_regions([GenomicRegion('chr1', 1, 1000, a=1, b='a'),
                          GenomicRegion('chr1', 1001, 2000, a=2, b='b'),
                          GenomicRegion('chr1', 2001, 3000, a=3, b='c'),
                          GenomicRegion('chr2', 1, 1000, a=4, b='d')])


class TestVectorArchitecturalRegionFeature:
    def setup_method(self, method):
        self.vaf = VAF(data_fields={'a': t.Int32Col(), 'b': t.StringCol(10)})

    def teardown_method(self, method):
        self.vaf.close()

    def test_get_rows(self):
        assert isinstance(self.vaf[0], GenomicRegion)
        assert self.vaf[0].a == 1
        assert self.vaf[0].b == 'a'

        regions = self.vaf[1:3]
        assert isinstance(regions, types.GeneratorType)
        for i, r in enumerate(regions):
            assert r.chromosome == 'chr1'
            assert r.a == i+2
            assert r.b == 'abcd'[i+1]

        regions = self.vaf['chr1']
        assert isinstance(regions, types.GeneratorType)
        for i, r in enumerate(regions):
            assert r.chromosome == 'chr1'
            assert r.a == i+1
            assert r.b == 'abcd'[i]

        regions = self.vaf['chr1:1-2000']
        assert isinstance(regions, types.GeneratorType)
        for i, r in enumerate(regions):
            assert r.chromosome == 'chr1'
            assert r.a == i+1
            assert r.b == 'abcd'[i]
        regions = self.vaf['chr1:1-2000']
        assert len(list(regions)) == 2

        regions = self.vaf['chr1:1-2001']
        assert isinstance(regions, types.GeneratorType)
        for i, r in enumerate(regions):
            assert r.chromosome == 'chr1'
            assert r.a == i+1
            assert r.b == 'abcd'[i]
        regions = self.vaf['chr1:1-2001']
        assert len(list(regions)) == 3

        regions = self.vaf[GenomicRegion(start=1, end=2000, chromosome=None)]
        assert isinstance(regions, types.GeneratorType)
        for i, r in enumerate(regions):
            if i < 2:
                assert r.chromosome == 'chr1'
                assert r.a == i+1
                assert r.b == 'abcd'[i]
            else:
                assert r.chromosome == 'chr2'
                assert r.a == 4
                assert r.b == 'd'

        regions = self.vaf[GenomicRegion(start=1, end=2000, chromosome=None)]
        assert len(list(regions)) == 3

        regions = self.vaf[GenomicRegion(start=None, end=None, chromosome=None)]
        assert len(list(regions)) == 4

    def test_get_columns(self):
        # let's test single ones
        results = self.vaf[0, 'a']
        assert results == 1
        results = self.vaf[1, 'b']
        assert results == 'b'
        results = self.vaf[2, 'chromosome']
        assert results == 'chr1'

        # int
        results = self.vaf['chr1', 1]  # chromosome
        assert isinstance(results, list)
        assert np.array_equal(['chr1', 'chr1', 'chr1'], results)

        results = self.vaf['chr1', 6]  # b
        assert isinstance(results, list)
        assert np.array_equal(['a', 'b', 'c'], results)

        # str
        results = self.vaf['chr1', 'chromosome']  # chromosome
        assert isinstance(results, list)
        assert np.array_equal(['chr1', 'chr1', 'chr1'], results)

        results = self.vaf['chr1', 'b']  # b
        assert isinstance(results, list)
        assert np.array_equal(['a', 'b', 'c'], results)

        # slice
        results = self.vaf['chr1', 5:7]  # a, b
        assert isinstance(results, dict)
        assert 'a' in results
        assert 'b' in results
        assert np.array_equal([1, 2, 3], results['a'])
        assert np.array_equal(['a', 'b', 'c'], results['b'])

        # list
        results = self.vaf['chr1', ['a', 'b']]  # a, b
        assert isinstance(results, dict)
        assert 'a' in results
        assert 'b' in results
        assert np.array_equal([1, 2, 3], results['a'])
        assert np.array_equal(['a', 'b', 'c'], results['b'])

    def test_setitem(self):
        assert self.vaf[0, 'a'] == 1
        self.vaf[0, 'a'] = 9
        assert self.vaf[0, 'a'] == 9

        assert self.vaf[2, 'chromosome'] == 'chr1'
        self.vaf[2, 'chromosome'] = 'chr2'
        assert self.vaf[2, 'chromosome'] == 'chr2'
        self.vaf[2, 'chromosome'] = 'chr1'

        assert np.array_equal(['a', 'b', 'c'], self.vaf['chr1', 6])
        self.vaf['chr1', 6] = ['d', 'e', 'f']
        assert np.array_equal(['d', 'e', 'f'], self.vaf['chr1', 6])


class MAF(MatrixArchitecturalRegionFeature):
    """
    This only exists so we can instantiate a MatrixArchitecturalRegionFeature
    for testing.
    """
    def __init__(self, file_name=None, mode='a', data_fields=None,
                 regions=None, edges=None, tmpdir=None):
        MatrixArchitecturalRegionFeature.__init__(self, file_name=file_name, mode=mode, data_fields=data_fields,
                                                  regions=regions, edges=edges, tmpdir=tmpdir)

    def _calculate(self, *args, **kwargs):
        for i in range(10):
            if i < 5:
                chromosome = 'chr1'
                start = i*1000
                end = (i+1)*1000
            elif i < 8:
                chromosome = 'chr2'
                start = (i-5)*1000
                end = (i+1-5)*1000
            else:
                chromosome = 'chr3'
                start = (i-8)*1000
                end = (i+1-8)*1000
            node = Node(chromosome=chromosome, start=start, end=end)
            self.add_region(node, flush=False)
        self.flush()

        for i in range(10):
            for j in range(i, 10):
                edge = Edge(source=i, sink=j, weight=i*j, foo=i, bar=j, baz='x' + str(i*j))
                self.add_edge(edge, flush=False)
        self.flush()

    @calculateondemand
    def foo(self, key=None):
        return self.as_matrix(key, values_from='foo')


class TestMatrixArchitecturalRegionFeature:
    def setup_method(self, method):
        self.maf = MAF(data_fields={'foo': t.Int32Col(pos=0),
                                    'bar': t.Float32Col(pos=1),
                                    'baz': t.StringCol(50, pos=2)})

    def teardown_method(self, method):
        self.maf.close()

    def test_edges(self):
        assert len(self.maf.edges()) == 55


