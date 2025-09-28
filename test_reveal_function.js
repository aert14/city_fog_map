// Simple test to reproduce the reveal district issue
function testDestructuring() {
  console.log('Testing destructuring...');

  // Simulate the mockParams
  const mockParams = {
    updateHexagonsFromServer: async () => console.log('updateHexagonsFromServer'),
    addToSpatialIndex: (hex) => console.log('addToSpatialIndex', hex),
    updateDistrictProgress: (district, okrug) => console.log('updateDistrictProgress'),
    countEl: { textContent: '0' },
    forceFogRedraw: () => console.log('forceFogRedraw'),
    allKnownHexagons: new Set()
  };

  console.log('mockParams.allKnownHexagons:', mockParams.allKnownHexagons);
  console.log('typeof allKnownHexagons:', typeof mockParams.allKnownHexagons);
  console.log('has has method:', typeof mockParams.allKnownHexagons.has);

  // Simulate the function call
  const districtId = 123;

  // Simulate revealEntireDistrict
  const params = mockParams;
  const { updateHexagonsFromServer, addToSpatialIndex, updateDistrictProgress, countEl, forceFogRedraw, allKnownHexagons } = params;

  console.log('Destructured allKnownHexagons:', allKnownHexagons);
  console.log('Destructured allKnownHexagons type:', typeof allKnownHexagons);

  // Simulate calling revealDistrictViaVisits
  const subParams = {
    addToSpatialIndex,
    updateDistrictProgress,
    countEl,
    forceFogRedraw,
    allKnownHexagons
  };

  const { addToSpatialIndex: subAddToSpatialIndex, updateDistrictProgress: subUpdateDistrictProgress, countEl: subCountEl, forceFogRedraw: subForceFogRedraw, allKnownHexagons: subAllKnownHexagons } = subParams;

  console.log('Sub-destructured allKnownHexagons:', subAllKnownHexagons);
  console.log('Sub-destructured allKnownHexagons type:', typeof subAllKnownHexagons);

  // Test the has method
  try {
    const result = subAllKnownHexagons.has('test');
    console.log('has method works:', result);
  } catch (error) {
    console.error('Error calling has:', error);
  }
}

testDestructuring();
